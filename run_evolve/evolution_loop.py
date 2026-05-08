"""Per-challenge evolutionary loop driving generations of agent variants."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from cyber_evolver.evolve.orchestrator import EvolutionOrchestrator
    from cyber_evolver.evolve.scheduler import TaskScheduler
    from cyber_evolver.evolve.selector import BaseSelector
    from cyber_evolver.evolve.evolution_node import EvolutionNode


class EvolutionLoop:
    def __init__(
        self,
        orchestrator: "EvolutionOrchestrator",
        scheduler: "TaskScheduler",
        selector: "BaseSelector",
        evo_config: Dict[str, Any],
        chal_id,
        chal_data,
        chal_logger,
        success_threshold=0.3,
    ):
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.selector = selector
        self.logger = chal_logger
        self.config = evo_config
        self.chal_id = chal_id
        self.chal_data = chal_data
        self.success_threshold = success_threshold

        self.generation = 0
        self.current_nodes: List["EvolutionNode"] = []

        self.final_result = {
            "status": "completed",
            "generations": 0,
            "best_success_rate": 0.0,
            "best_node_id": None,
            "early_stop_at": None,
        }

    def _check_target_status(self, location_msg: str) -> bool:
        """Return True if the challenge target has entered the 'stopped' state."""
        status = self.chal_data.get("target_status", "")
        if status == "stopped":
            self.logger.error(
                "🛑 ABORTING: Challenge '%s' target_status is 'stopped' (%s). "
                "This indicates an environment failure.",
                self.chal_id, location_msg
            )
            self.final_result.update({
                "status": "aborted_target_stopped",
                "error": f"Target stopped unexpectedly at {location_msg}"
            })
            return True
        return False

    def run(self):
        self.current_nodes = self.orchestrator.init_generation_zero(self.chal_data)
        self.logger.info(f"Initialized Generation 0 with {len(self.current_nodes)} node(s).")

        for gen in range(self.config["max_generations"]):
            self.generation = gen
            if self._check_target_status(f"End of Generation {gen}"):
                return self.final_result
            self._run_generation()

            if not self.current_nodes or self.final_result.get("early_stop_at") is not None:
                break

        if self.final_result.get("early_stop_at") is None and self.current_nodes:
            best_node = max(self.current_nodes, key=lambda n: n.success_rate)
            self.final_result.update({
                "best_success_rate": best_node.success_rate,
                "best_node_id": best_node.node_id,
                "generations": self.generation + 1,
            })
        elif not self.current_nodes and self.final_result["status"] == "completed":
            self.final_result["status"] = "extinct"
        self.logger.info("🏁 Evolution completed successfully.")
        return self.final_result

    def _run_generation(self):
        from cyber_evolver.evolve.scheduler import TaskSpec

        gen_id = self.generation
        logger = self.logger

        logger.info("=" * 60)
        logger.info("🧬 Generation %d (%d nodes)", gen_id, len(self.current_nodes))
        logger.info("=" * 60)

        tasks: List[TaskSpec] = []
        for node in self.current_nodes:
            for i in range(self.config["samples_per_node"]):
                tasks.append(TaskSpec(
                    node=node,
                    chal_id=self.chal_id,
                    chal_data=self.chal_data,
                    sample_id=i
                ))
        logger.info(f"  📋 Queued {len(tasks)} evaluation tasks.")
        logger.info(
            "⏳ Waiting for scheduler.submit_tasks() to return before evaluating generation-level early stop."
        )
        results = self.scheduler.submit_tasks(tasks)
        logger.info(
            "✅ scheduler.submit_tasks() returned %d results for generation %d.",
            len(results),
            gen_id,
        )

        gen_stats_path = Path(self.orchestrator.root_dir) / f"gen_{gen_id}_stats.json"
        gen_stats_path.write_text(
            json.dumps([asdict(n) for n in self.current_nodes], indent=2),
            encoding="utf-8"
        )

        best_node = max(self.current_nodes, key=lambda n: n.success_rate)
        best_sr = best_node.success_rate
        logger.info("success_rates=%s", [n.success_rate for n in self.current_nodes])
        if best_sr >= self.success_threshold:
            self.logger.info(
                "🎉 Early stop triggered! Node with SR=%.1f%% >= %.1f%%",
                best_sr * 100, self.success_threshold * 100
            )
            self.final_result.update({
                "early_stop_at": gen_id,
                "best_success_rate": best_sr,
                "best_node_id": best_node.node_id,
                "generations": gen_id + 1,
            })
            self._log_generation_stats([])
            return

        # Last generation — no next gen to consume analysis, skip expensive LLM work
        is_last_generation = gen_id >= self.config["max_generations"] - 1
        if is_last_generation:
            logger.info("⏭️ Last generation — skipping analysis & mutation.")
            self._log_generation_stats([])
            self.current_nodes = []
            return

        logger.info("🔍 Analyzing logs and calculating assessment scores...")
        def analyze_node(node):
            if node.is_code_valid:
                analysis_result = self.orchestrator.log_analyzer.run_batch_parallel(
                    node.logs_path,
                    max_workers=self.config["samples_per_node"]
                )
                node.assessment_score = analysis_result["average_score"]
                return node.node_id, node.assessment_score
            else:
                return node.node_id, 0
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_node = {executor.submit(analyze_node, node): node for node in self.current_nodes}

            for future in as_completed(future_to_node):
                try:
                    nid, score = future.result()
                    logger.debug(f"Node {nid} analyzed. Assessment Score: {score:.2f}")
                except Exception as e:
                    logger.error(f"Error analyzing node: {e}", exc_info=True)

        selected_nodes: List["EvolutionNode"] = []
        top_k = self.config["top_k_selection"]

        candidates = self.current_nodes
        selection = self.selector.select(candidates, k=top_k)

        selected_nodes.extend(selection.selected)

        self._log_generation_stats(selected_nodes)

        if selected_nodes:
            logger.info("🔄 Mutating %d selected parents...", len(selected_nodes))
            next_nodes = self.orchestrator.create_next_generation(
                parents=selected_nodes,
                children_per_node=self.config["children_per_node"],
                sample_child=self.config["sample_plan_num"]
            )
            logger.info("✅ Generated %d child nodes.", len(next_nodes))
            self.current_nodes = next_nodes
        else:
            self.current_nodes = []

    def _log_generation_stats(self, selected_nodes: List["EvolutionNode"]):
        """📊 Print a clean results dashboard for this generation.

        Args:
            selected_nodes: Nodes selected to survive to next generation.
        """
        logger = self.logger
        gen = self.generation
        selected_ids = {n.node_id for n in selected_nodes}

        logger.info("=" * 70)
        logger.info("📈 GENERATION %d RESULTS — %d nodes evaluated", gen, len(self.current_nodes))
        logger.info("=" * 70)

        if not self.current_nodes:
            logger.info("⚠️  No nodes evaluated.")
            return

        sorted_nodes = sorted(
            self.current_nodes,
            key=lambda n: (-n.success_rate, n.avg_steps, n.node_id)
        )

        header = f"{' ':1} {'Node ID':<12} | {'SR':>6} | {'Score':>5} | {'Steps':>6} | {'Tokens':>7}"
        logger.info(header)
        logger.info("-" * len(header))

        for node in sorted_nodes:
            sr_str = f"{node.success_rate * 100:5.1f}%" if node.total_runs > 0 else "—"
            score_str = f"{node.assessment_score:5.1f}"  # Show the integer score
            steps_str = f"{node.avg_steps:5.1f}" if node.total_runs > 0 else "—"
            tokens_str = f"{node.avg_token_num:6.0f}" if node.total_runs > 0 else "—"

            mark = "★" if node.node_id in selected_ids else " "
            logger.info(
                "%s %-12s | %6s | %5s | %6s | %7s",
                mark, node.node_id, sr_str, score_str, steps_str, tokens_str
            )

        logger.info("-" * len(header))
        if selected_ids:
            logger.info("🏆 %d selected for next generation: %s",
                        len(selected_ids), ", ".join(sorted(selected_ids)))
        else:
            logger.info("💀 No nodes selected — evolution halted.")
        logger.info("")
