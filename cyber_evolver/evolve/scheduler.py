import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, Future
from typing import List, Tuple, Dict, Any, Callable, Optional
from dataclasses import dataclass
from .node import EvolutionNode
from common.utils.worker_diagnostics import (
    format_scheduler_interrupt_message,
    format_scheduler_result_error_message,
    format_task_completion_message,
)
from common.utils.safe_logging import safe_format_exception, safe_log_exception


@dataclass
class TaskSpec:
    node: EvolutionNode
    chal_id: str
    chal_data: Dict[str, Any]
    sample_id: int

@dataclass
class TaskResult:
    node_id: str
    chal_id: str
    sample_id: int
    success: bool
    steps: int
    token_num: int
    duration: float
    error: Optional[str] = None


class TaskScheduler:
    def __init__(
        self,
        task_fn: Callable,
        max_workers: int,
        logger: logging.Logger = None,
    ):
        self.task_fn = task_fn
        self.max_workers = max_workers
        self.logger = logger or logging.getLogger(self.__class__.__name__)
        self._executor = None

    def submit_tasks(self, tasks: List[TaskSpec]) -> List[TaskResult]:
        if not tasks:
            self.logger.info("No tasks to run.")
            return []

        results: List[TaskResult] = []
        total = len(tasks)
        completed = 0

        # Optional: log start
        self.logger.info("▶️ Starting %d tasks with %d workers...", total, self.max_workers)
        self.logger.info(
            "⏳ Waiting for task futures to complete before generation-level early-stop evaluation."
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            self._executor = executor
            future_map = {
                executor.submit(self._safe_task_wrapper, task): task
                for task in tasks
            }

            try:
                start_time = time.time()
                for future in as_completed(future_map):
                    task = future_map[future]
                    self.logger.info(
                        "🔔 Future ready: node=%s sample=%d",
                        task.node.node_id,
                        task.sample_id,
                    )
                    try:
                        res = future.result()
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as e:
                        safe_log_exception(
                            self.logger,
                            format_scheduler_result_error_message(
                                node_id=task.node.node_id,
                                sample_id=task.sample_id,
                                stage="future_result",
                                exc=e,
                            ),
                            exc=e,
                        )
                        raise

                    try:
                        results.append(res)
                        completed += 1

                        self.logger.info(
                            format_task_completion_message(
                                node_id=res.node_id,
                                sample_id=res.sample_id,
                                completed=completed,
                                total=total,
                                success=res.success,
                                steps=res.steps,
                                duration_s=res.duration,
                                token_num=res.token_num,
                            )
                        )

                        # 📊 Progress logging: every 10%, or every 10 tasks, or last one
                        if (
                            completed % max(1, total // 10) == 0  # every ~10%
                            or completed == total                # last task
                            or completed <= 5                    # first 5 for quick feedback
                        ):
                            elapsed = time.time() - start_time
                            eta = (elapsed / completed) * (total - completed) if completed else 0
                            self.logger.info(
                                "Progress: %d/%d (%.1f%%) | Elapsed: %.1fs | ETA: %.1fs",
                                completed, total, 100 * completed / total, elapsed, eta
                            )

                        # Update node stats
                        node = task.node
                        node.total_runs += 1
                        if res.success:
                            node.success_count += 1
                        node.success_rate = node.success_count / node.total_runs
                        node.avg_steps += res.steps
                        node.avg_duration += res.duration
                        node.avg_token_num += res.token_num
                    except (KeyboardInterrupt, SystemExit):
                        raise
                    except BaseException as e:
                        safe_log_exception(
                            self.logger,
                            format_scheduler_result_error_message(
                                node_id=task.node.node_id,
                                sample_id=task.sample_id,
                                stage="result_processing",
                                exc=e,
                            ),
                            exc=e,
                        )
                        raise

                self.logger.info("✅ All %d tasks completed in %.2f seconds.", total, time.time() - start_time)

            except (KeyboardInterrupt, SystemExit):
                safe_log_exception(self.logger, "🛑 Interrupted. Cancelling pending tasks...")
                pending_tasks = [
                    f"{future_map[f].node.node_id}/sample-{future_map[f].sample_id}"
                    for f in future_map
                    if not f.done()
                ]
                self.logger.warning(
                    format_scheduler_interrupt_message(
                        completed=completed,
                        total=total,
                        pending_tasks=pending_tasks,
                    )
                )
                # Cancel pending futures
                for f in future_map:
                    if not f.done():
                        f.cancel()

                # Wait up to 3s for cancellation to take effect
                done, not_done = wait(list(future_map.keys()), timeout=1)
                for f in not_done:
                    self.logger.error("❌ Failed to cancel task: %s", future_map[f])

                executor.shutdown(wait=False, cancel_futures=True)
                raise

            finally:
                self._executor = None

        return results

    def _safe_task_wrapper(self, task: TaskSpec) -> TaskResult:
        st = time.time()
        try:
            raw_res = self.task_fn(
                node=task.node,
                chal_id=task.chal_id,
                chal_data=task.chal_data,
                sample_id=task.sample_id,
            )
            return TaskResult(
                node_id=raw_res["node_id"],
                chal_id=raw_res["chal_id"],
                sample_id=task.sample_id,
                success=raw_res["success"],
                steps=raw_res["steps"],
                token_num=raw_res["token_num"],
                duration=raw_res["duration"],
                error=raw_res.get("error")
            )
        except Exception as e:
            safe_log_exception(
                self.logger,
                "Task failed: node=%s, chal=%s, sample=%d",
                task.node.node_id,
                task.chal_id,
                task.sample_id,
                exc=e,
            )
            return TaskResult(
                node_id=task.node.node_id,
                chal_id=task.chal_id,
                sample_id=task.sample_id,
                success=False,
                steps=0,
                token_num=0,
                duration=time.time() - st,
                error=f"Crash: {safe_format_exception(e)}"
            )
        except BaseException as e:
            safe_log_exception(
                self.logger,
                "Task aborted by BaseException: node=%s, chal=%s, sample=%d",
                task.node.node_id,
                task.chal_id,
                task.sample_id,
                exc=e,
            )
            raise
