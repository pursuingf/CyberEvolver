import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
from jinja2 import Template
from langchain_core.messages import SystemMessage, HumanMessage
from common.utils.util import parse_agent_log, load_prompt_config, llm_invoke
from common.llm_dispatch.dispatcher import LLMDispatcherFatalError
from collections import defaultdict
import re
import statistics

class LogAnalyzer:
    def __init__(self, llm, prompt_cfg_path: str = "cyber_evolver/evolve/prompt.yml", logger=None, ablation_mode: str = "none"):
        self.llm = llm
        self.prompt_cfg_path = prompt_cfg_path
        self.logger = logger or logging.getLogger("LogAnalyzer")
        self.config = load_prompt_config(prompt_cfg_path)
        self.ablation_mode = (ablation_mode or "none").strip().lower()

    def reload_prompts(self):
        self.logger.info("🔄 Reloading prompts...")
        self.config = load_prompt_config(self.prompt_cfg_path)

    def _looks_like_python_action(self, action: str) -> bool:
        if not isinstance(action, str) or not action.strip():
            return False
        python_prefixes = (
            "import ",
            "from ",
            "def ",
            "class ",
            "print(",
            "return ",
            "try:",
            "with ",
            "if ",
            "for ",
            "while ",
            "elif ",
            "else:",
            "except ",
            "if __name__",
        )
        for line in action.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith(python_prefixes):
                return True
        return False

    def _sanitize_action_for_summary(self, action: str) -> str:
        if not isinstance(action, str) or not action:
            return action
        if not self._looks_like_python_action(action):
            return action
        return "\n".join(
            line for line in action.splitlines()
            if not line.lstrip().startswith("#")
        )

    def _base_llm_call(self, sys_key: str, user_key: str, render_kwargs: Dict) -> str:
        sys_tmpl = self.config.get(sys_key, "")
        user_tmpl = self.config.get(user_key, "")
        
        # if not sys_tmpl or not user_tmpl:
        #     raise ValueError(f"Missing prompt config for keys: {sys_key} / {user_key}")

        sys_msg = Template(sys_tmpl).render(**render_kwargs)
        user_msg = Template(user_tmpl).render(**render_kwargs)
        
        # Invoke
        resp = llm_invoke(
            self.llm,
            [SystemMessage(content=sys_msg), HumanMessage(content=user_msg)],
            meta={
                "component": "cyber_evolver.evolve.loganalyzer",
                "sys_key": sys_key,
                "user_key": user_key,
            },
        )
        return resp.content.strip()

    def _safe_json_parse(self, text: str) -> Union[Dict, List, str]:
        try:
            clean = text
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0]
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0]
            return json.loads(clean.strip())
        except Exception:
            self.logger.exception(f"Failed to parse JSON, returning raw text. Content preview: {text}...")
            return text

    def _parse_text_struct(self, text: str) -> Dict:
        steps = []
        
        # 1. First locate every STEP anchor.
        # The regex is intentionally permissive:
        # (?:^|\n) ensures a line boundary so body text is less likely to match.
        # [^\w\n]* allows decorative prefixes such as ===, **, ###, or ---.
        # STEP\s*(\d+) captures the STEP marker and number.
        # [^\n]* consumes the rest of the decorated header line.
        step_header_pattern = re.compile(
            r'(?:^|\n)\s*(?:={3,}|#{3,}|\*{2,}|-{3,}|[\(\[])?\s*STEP\s*(\d+).*?', 
            re.IGNORECASE
        )
        
        # Locate every STEP header.
        matches = list(step_header_pattern.finditer(text))
        
        for i, match in enumerate(matches):
            try:
                # Current step number.
                idx = int(match.group(1))
                
                # Determine this block's boundaries.
                start_pos = match.end()  # Start right after the header.
                
                # End at the next STEP header, or at the end of text.
                if i + 1 < len(matches):
                    end_pos = matches[i+1].start()
                else:
                    end_pos = len(text)
                
                # Extract the complete text block for this step.
                block_content = text[start_pos:end_pos]
                
                # 2. Extract THOUGHT and OBSERVATION within the block.
                # Block-local extraction is much more stable than a global regex.
                
                # Match THOUGHT variants such as **THOUGHT**, ## THOUGHT, THOUGHT:, or Thought.
                # Use non-greedy matching until OBSERVATION or the end of the block.
                thought_match = re.search(
                    r'(?:[\*#-]*)?\s*THOUGHT\s*(?:[\*#-]*)?\s*:?\s*(.*?)\s*(?=(?:[\*#-]*)?\s*OBSERVATION|\Z)',
                    block_content,
                    re.IGNORECASE | re.DOTALL
                )
                
                # Match OBSERVATION variants in the same style.
                obs_match = re.search(
                    r'(?:[\*#-]*)?\s*OBSERVATION\s*(?:[\*#-]*)?\s*:?\s*(.*)',
                    block_content,
                    re.IGNORECASE | re.DOTALL
                )
                
                # Extract content, using an empty string when a section is absent.
                thought_text = thought_match.group(1).strip() if thought_match else ""
                obs_text = obs_match.group(1).strip() if obs_match else ""
                
                # Fallback: when both sections are missing, keep the whole block as thought.
                if not thought_text and not obs_text and block_content.strip():
                    thought_text = block_content.strip()

                steps.append({
                    "step_index": idx,
                    "thought": thought_text,
                    "observation": obs_text
                })
                
            except Exception as e:
                self.logger.warning(f"Error parsing text block for Step {match.group(1)}: {e}")
                continue
                
        if not steps:
            return {"steps": [], "raw_text": text}
        return {"steps": steps}
            
    def summarize_thought_obs(self, raw_log: str, max_step:int, **kwargs) -> Dict:
        """Original method for backward compatibility"""
        return self.summarize_thought_obs_chunked(raw_log, max_step, chunk_size=10)

    def summarize_thought_obs_chunked(self, raw_log: str, max_step: int, chunk_size: int = 10, **kwargs) -> Dict:
        """
        Summarize log in chunks to avoid LLM hallucinations and context limits.
        Process chunks in parallel for better performance.

        Args:
            raw_log: Complete raw log content
            max_step: Total number of steps in the log
            chunk_size: Number of steps to process per chunk (default: 10)

        Returns:
            Dict with parsed structure containing all steps
        """
        self.logger.info(f"📊 Starting parallel chunked summarization: {max_step} steps, chunk_size={chunk_size}")

        # Parse the raw log to extract individual steps
        parsed_log = self._parse_raw_log_to_steps(raw_log, max_step)

        if not parsed_log:
            self.logger.warning("No steps found in log, falling back to original method")
            return self._summarize_thought_obs_original(raw_log, max_step)

        # Prepare all chunks for parallel processing
        chunks = []
        for chunk_start in range(1, max_step + 1, chunk_size):
            chunk_end = min(chunk_start + chunk_size - 1, max_step)

            # Extract raw content for this chunk
            chunk_content = self._extract_chunk_content(parsed_log, chunk_start, chunk_end)

            if not chunk_content:
                self.logger.warning(f"No content found for chunk {chunk_start}-{chunk_end}, skipping")
                continue

            # Get previous chunk's raw content for context (if exists)
            previous_raw_context = self._get_previous_raw_context(parsed_log, chunk_start, chunk_size)

            chunks.append({
                'start': chunk_start,
                'end': chunk_end,
                'content': chunk_content,
                'previous_raw_context': previous_raw_context
            })

        self.logger.info(f"Prepared {len(chunks)} chunks for parallel processing")

        # Process chunks in parallel
        all_summarized_steps = self._process_chunks_parallel(chunks, max_step)

        # Validate and return final structure
        return self._validate_and_merge_steps(all_summarized_steps, max_step)

    def _get_previous_raw_context(self, parsed_log: Dict[int, str], current_start: int, chunk_size: int) -> str:
        """Get raw content from previous chunk for context."""
        if current_start <= 1:
            return "No previous context (first chunk)"

        prev_start = max(1, current_start - chunk_size)
        prev_end = current_start - 1

        context_lines = [f"Previous chunk raw content (steps {prev_start}-{prev_end}):"]

        for step in range(prev_start, prev_end + 1):
            if step in parsed_log:
                # Extract just the thought/action part for context (not full observation)
                step_content = parsed_log[step]
                # Take first few lines as context preview
                lines = step_content.split('\n')[:3]
                preview = ' '.join(lines)[:200]
                if len(preview) == 200:
                    preview += "..."
                context_lines.append(f"  Step {step}: {preview}")

        return '\n'.join(context_lines)

    def _process_chunks_parallel(self, chunks: List[Dict], total_steps: int) -> List[Dict]:
        """Process all chunks in parallel using ThreadPoolExecutor."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        all_summarized_steps = []

        with ThreadPoolExecutor(max_workers=min(len(chunks), 10)) as executor:
            # Submit all chunk processing tasks
            future_to_chunk = {}
            for chunk in chunks:
                future = executor.submit(
                    self._process_single_chunk,
                    chunk['content'],
                    chunk['start'],
                    chunk['end'],
                    total_steps,
                    chunk['previous_raw_context']
                )
                future_to_chunk[future] = chunk

            # Collect results as they complete
            completed_count = 0
            for future in as_completed(future_to_chunk):
                chunk = future_to_chunk[future]
                try:
                    chunk_result = future.result()
                    if chunk_result:
                        all_summarized_steps.extend(chunk_result)
                        completed_count += 1
                        self.logger.info(f"✅ Processed chunk {chunk['start']}-{chunk['end']}: {len(chunk_result)} steps summarized")
                    else:
                        self.logger.warning(f"⚠️ Chunk {chunk['start']}-{chunk['end']} returned no results")
                except Exception as e:
                    if isinstance(e, LLMDispatcherFatalError):
                        raise
                    self.logger.error(f"❌ Failed to process chunk {chunk['start']}-{chunk['end']}: {e}", exc_info=True)

            self.logger.info(f"📈 Parallel processing completed: {completed_count}/{len(chunks)} chunks successful")

        return all_summarized_steps

    def _process_single_chunk(self, chunk_content: str, start_step: int, end_step: int,
                             total_steps: int, previous_raw_context: str) -> List[Dict]:
        """Process a single chunk (called in parallel)."""
        try:
            # Call LLM for this chunk
            chunk_summary = self._summarize_chunk(
                chunk_content, start_step, end_step, total_steps, previous_raw_context
            )

            # Parse the chunk summary
            chunk_parsed = self._parse_text_struct(chunk_summary)

            if "steps" in chunk_parsed:
                # Filter out steps outside the assigned range (LLM sometimes regenerates previous context)
                in_range = [
                    s for s in chunk_parsed["steps"]
                    if start_step <= (s.get("step_index", 0)) <= end_step
                ]
                if len(in_range) < len(chunk_parsed["steps"]):
                    self.logger.debug(
                        "Chunk %d-%d: filtered %d out-of-range steps (kept %d)",
                        start_step, end_step,
                        len(chunk_parsed["steps"]) - len(in_range),
                        len(in_range),
                    )
                return in_range
            else:
                self.logger.warning(f"Chunk {start_step}-{end_step}: No steps in parsed result")
                return []

        except LLMDispatcherFatalError:
            raise
        except Exception as e:
            self.logger.error(f"Error processing chunk {start_step}-{end_step}: {e}")
            # Return placeholder steps for this chunk
            return self._create_placeholder_steps(start_step, end_step, str(e))

    def _create_placeholder_steps(self, start_step: int, end_step: int, error_msg: str) -> List[Dict]:
        """Create placeholder steps when chunk processing fails."""
        placeholders = []
        for step in range(start_step, end_step + 1):
            placeholders.append({
                "step_index": step,
                "thought": f"[Step {step} summary failed: {error_msg}]",
                "observation": ""
            })
        return placeholders

    def _summarize_thought_obs_original(self, raw_log: str, max_step: int) -> Dict:
        """Original implementation for fallback"""
        raw_res = self._base_llm_call(
            sys_key="system_prompt_thought_obs_summarizer",
            user_key="user_prompt_thought_obs_summarizer",
            render_kwargs={
                "raw_content": raw_log,
                "max_step": max_step
            }
        )
        self.logger.debug(f"Original summarization result: {raw_res[:500]}...")
        return self._parse_text_struct(raw_res)

    def _parse_raw_log_to_steps(self, raw_log: str, max_step: int) -> Dict[int, str]:
        """
        Parse raw log into a dictionary mapping step numbers to their content.

        Returns:
            Dict[int, str]: {step_number: step_content}
        """
        steps = {}

        # Split log by step markers
        step_pattern = re.compile(r'--- Step (\d+)/\d+ ---')
        lines = raw_log.split('\n')

        current_step = None
        current_content = []

        for line in lines:
            step_match = step_pattern.search(line)
            if step_match:
                # Save previous step if exists
                if current_step is not None and current_content:
                    steps[current_step] = '\n'.join(current_content)

                # Start new step
                current_step = int(step_match.group(1))
                current_content = [line]
            elif current_step is not None:
                current_content.append(line)

        # Save the last step
        if current_step is not None and current_content:
            steps[current_step] = '\n'.join(current_content)

        self.logger.debug(f"Parsed {len(steps)} steps from raw log")
        return steps

    def _extract_chunk_content(self, parsed_log: Dict[int, str], start_step: int, end_step: int) -> str:
        """Extract content for a specific chunk of steps."""
        chunk_lines = []
        for step in range(start_step, end_step + 1):
            if step in parsed_log:
                chunk_lines.append(parsed_log[step])
            else:
                self.logger.warning(f"Step {step} not found in parsed log")
                chunk_lines.append(f"--- Step {step}/? ---\n[Step {step} content missing]")

        return '\n'.join(chunk_lines)

    def _summarize_chunk(self, chunk_content: str, start_step: int, end_step: int,
                        total_steps: int, previous_raw_context: str) -> str:
        """Call LLM to summarize a chunk of steps."""
        raw_res = self._base_llm_call(
            sys_key="system_prompt_thought_obs_summarizer_chunk",
            user_key="user_prompt_thought_obs_summarizer_chunk",
            render_kwargs={
                "raw_content": chunk_content,
                "start_step": start_step,
                "end_step": end_step,
                "total_steps": total_steps,
                "previous_context": previous_raw_context  # Using raw context instead of summarized context
            }
        )
        self.logger.debug(f"Chunk {start_step}-{end_step} summary length: {len(raw_res)} chars")
        return raw_res

    def _create_context_summary(self, chunk_parsed: Dict, start_step: int, end_step: int) -> str:
        """Create a brief context summary for the next chunk."""
        if "steps" not in chunk_parsed or not chunk_parsed["steps"]:
            return f"Steps {start_step}-{end_step}: No steps summarized"

        # Take the last 2 steps as context
        context_steps = chunk_parsed["steps"][-2:] if len(chunk_parsed["steps"]) >= 2 else chunk_parsed["steps"]

        context_lines = [f"Previous chunk ({start_step}-{end_step}):"]
        for step in context_steps:
            step_idx = step.get("step_index", "?")
            thought_preview = step.get("thought", "")[:100]
            if len(thought_preview) == 100:
                thought_preview += "..."
            context_lines.append(f"  Step {step_idx}: {thought_preview}")

        return '\n'.join(context_lines)

    def _validate_and_merge_steps(self, all_steps: List[Dict], max_step: int) -> Dict:
        """Validate and merge all summarized steps."""
        if not all_steps:
            return {"steps": [], "raw_text": "No steps summarized"}

        # Sort by step index
        all_steps.sort(key=lambda x: x.get("step_index", 0))

        # Validate step indices are sequential
        expected_step = 1
        validated_steps = []

        for step in all_steps:
            step_idx = step.get("step_index", 0)

            # Skip duplicate or out-of-order steps
            if step_idx < expected_step:
                self.logger.warning(f"Duplicate or out-of-order step {step_idx}, expected {expected_step}")
                continue

            # Handle missing steps
            while expected_step < step_idx:
                self.logger.warning(f"Missing step {expected_step}, adding placeholder")
                validated_steps.append({
                    "step_index": expected_step,
                    "thought": f"[Step {expected_step} summary missing from chunk processing]",
                    "observation": ""
                })
                expected_step += 1

            validated_steps.append(step)
            expected_step = step_idx + 1

        # Add placeholders for any remaining missing steps
        while expected_step <= max_step:
            self.logger.warning(f"Missing final step {expected_step}, adding placeholder")
            validated_steps.append({
                "step_index": expected_step,
                "thought": f"[Step {expected_step} summary missing from chunk processing]",
                "observation": ""
            })
            expected_step += 1

        self.logger.info(f"✅ Final validation: {len(validated_steps)} steps out of {max_step} expected")
        return {"steps": validated_steps}

    
    def _build_eureka_input(self, structure: Dict, raw_parsed_steps: List[Dict]) -> str:
        """Build Eureka input from summarized structure + middle 5 raw trajectory steps."""
        total_steps = len(raw_parsed_steps)
        sample_size = 5

        # --- Section 1: Middle 5 raw steps ---
        if total_steps <= sample_size:
            sample_steps = raw_parsed_steps
            section_title = f"## 1. Reference: Raw Trajectory (All {total_steps} Steps)"
            desc_text = f"> Unmodified raw content of the entire trajectory ({total_steps} steps).\n"
        else:
            start_idx = (total_steps - sample_size) // 2
            end_idx = start_idx + sample_size
            sample_steps = raw_parsed_steps[start_idx:end_idx]
            start_num = sample_steps[0].get("step", start_idx + 1)
            end_num = sample_steps[-1].get("step", end_idx)
            section_title = f"## 1. Reference: Raw Trajectory Sample (Middle 5 Steps)"
            desc_text = f"> Unmodified raw content of 5 steps from the middle of the trajectory (Step {start_num} to {end_num}).\n"

        md = [section_title, desc_text]
        for s in sample_steps:
            s_idx = s.get("step", "?")
            action_text = self._sanitize_action_for_summary(s.get('action', 'N/A'))
            md.append(f"[Step {s_idx}]")
            md.append(f"**Raw LLM Response:** {s.get('raw_response', 'N/A')}")
            md.append(f"**Phrased Action to Execute:**\n```bash\n{action_text}\n```")
            r_obs = s.get('observation', 'N/A')
            if len(r_obs) > 2000:
                r_obs = r_obs[:1999] + "... (raw truncation)"
            md.append(f"**Observation:**\n{r_obs}\n")

        md.append("---")

        # --- Section 2: Summarized structure ---
        md.append("## 2. Summarized Trajectory Structure")
        md.append("> Derived from LLM summarization of the raw logs.\n")

        if isinstance(structure, dict) and "steps" in structure:
            for step in structure["steps"]:
                s_idx = step.get("step_index") or step.get("step", "?")
                raw_action = self._sanitize_action_for_summary(step.get('raw_action', 'N/A'))
                md.append(f"[Summarized Step {s_idx}]")
                md.append(f"**Thought:** {step.get('thought', 'N/A')}")
                md.append(f"**Action:**\n```bash\n{raw_action}\n```")
                obs = step.get('observation', 'N/A')
                if len(obs) > 4000:
                    obs = obs[:3999] + "... (truncated)"
                md.append(f"**Observation:**\n{obs}\n")
        else:
            md.append(f"**Warning**: Structured data unavailable. Raw output: {str(structure)}")

        return "\n".join(md)

    def propose_eureka_idea(self, eureka_input: str, **kwargs) -> Dict:
        raw_res = self._base_llm_call(
            sys_key="system_prompt_eureka",
            user_key="user_prompt_eureka",
            render_kwargs={"raw_content": eureka_input}
        )
        final_score = 0
        score_hits = list(re.finditer(
            r"(?im)^\s*`?\s*\*{0,2}\s*SCORE\s*:\s*(\d{1,3})\s*\*{0,2}\s*`?\s*$",
            raw_res
        ))
        matched_str = score_hits[-1].group(1) if score_hits else None

        if not matched_str:
            score_hits2 = list(re.finditer(r"(?i)\bSCORE\s*:\s*(\d{1,3})\b", raw_res))
            matched_str = score_hits2[-1].group(1) if score_hits2 else None

        if not matched_str:
            tail_text = raw_res[-1500:]
            nums = re.findall(r"\b(\d{1,3})\b", tail_text)
            for num in reversed(nums):
                val = int(num)
                if 0 <= val <= 100:
                    matched_str = str(val)
                    break
                
        if matched_str:
            try:
                val = int(matched_str)
                if 0 <= val <= 100:
                    final_score = val
            except ValueError:
                pass
        else:
            tail_text = raw_res[-500:] 
            numbers = re.findall(r"\b(\d{1,3})\b", tail_text)
            if numbers:
                for num in reversed(numbers):
                    try:
                        val = int(num)
                        if 0 <= val <= 100:
                            final_score = val
                            break
                    except ValueError:
                        continue

        return raw_res, final_score

   
    def _enrich_structure_with_raw(self, analysis_data: Dict, parsed_steps_lookup: Dict[int, Dict]) -> Dict:
        if not isinstance(analysis_data, dict) or "steps" not in analysis_data:
            return analysis_data
        for item in analysis_data["steps"]:
            idx_val = item.get("step_index") or item.get("step")
            obs = item.get("observation", "")
            
            if idx_val is not None:
                if isinstance(obs, str) and "<OBS:" in obs:
                    try:
                        idx = int(idx_val)
                        if idx in parsed_steps_lookup:
                            raw_obs = parsed_steps_lookup[idx].get("observation", "")
                            # Optionally preserve description in comment? For now: replace fully.
                            # Example: "<OBS: content of main.c>" → actual file content
                            item["observation"] = item["observation"] + "**Important raw obs**:"+(raw_obs or obs)  # fallback to original if empty
                            self.logger.debug(f"Replaced <OBS> at step {idx} with raw observation (length: {len(raw_obs)})")
                        else:
                            self.logger.warning(f"Cannot enrich <OBS> at step {idx}: step not found in raw log")
                    except (ValueError, TypeError) as e:
                        self.logger.warning(f"Invalid step index {idx_val} for OBS enrichment: {e}")
                try:
                    idx = int(idx_val)
                    if idx in parsed_steps_lookup:
                        item["execute_time"] = parsed_steps_lookup[idx].get("execute_time", "")
                        item["raw_action"] = parsed_steps_lookup[idx].get("action", "")
                    else:
                        item["raw_action"] = "<Step index not found in raw log>"
                except ValueError:
                    continue
        
        return analysis_data

    def _enrich_global_actions_with_content(self, global_data: Dict, all_parsed_logs: Dict[str, Dict]) -> Dict:
        if not isinstance(global_data, dict) or "tactical_insights" not in global_data:
            return global_data

        for item in global_data["tactical_insights"]:
            traj_id = item.get("trajectory_id")
            idx_val = item.get("step_index")
            
            if traj_id and idx_val is not None:
                try:
                    idx = int(idx_val)
                    if traj_id in all_parsed_logs:
                        steps_lookup = {s["step"]: s for s in all_parsed_logs[traj_id]["steps"]}
                        
                        if idx in steps_lookup:
                            item["raw_action"] = steps_lookup[idx].get("action", "")
                        else:
                            item["raw_action"] = f"<Step {idx} not found in {traj_id}>"
                    else:
                        item["raw_action"] = f"<Log {traj_id} not found>"
                except Exception as e:
                    self.logger.warning(f"Enrichment failed for {traj_id} step {idx_val}: {e}")
                    
        return global_data
    
    def _format_markdown_report(self, log_name: str, structure: Dict, eureka_content: str, score: int, raw_parsed_steps: List[Dict]) -> str:
        total_steps = len(raw_parsed_steps)
        sample_size = 5

        if total_steps <= sample_size:
            sample_steps = raw_parsed_steps
            section_title = f"## 1. Reference: Raw Trajectory (All {total_steps} Steps)"
            desc_text = f"> This section contains the **unmodified** raw content of the entire trajectory ({total_steps} steps) to provide full context.\n"
        else:
            start_idx = (total_steps - sample_size) // 2
            end_idx = start_idx + sample_size
            sample_steps = raw_parsed_steps[start_idx:end_idx]
            
            start_num = sample_steps[0].get("step", start_idx + 1)
            end_num = sample_steps[-1].get("step", end_idx)
            
            section_title = f"## 1. Reference: Raw Trajectory Sample (Middle 5 Steps)"
            desc_text = f"> This section contains the **unmodified** raw content of 5 steps selected from the **middle** of the trajectory (Step {start_num} to {end_num}) to provide representative context.\n"

        md = [
            f"# LLM Analysis Report: {log_name}",
            f"> **Source Metadata**: This report is generated by a multi-stage analysis pipeline.",
            f"> **Global Assessment Score**: {score}/100\n",
            section_title,
            desc_text
        ]

        for s in sample_steps:
            s_idx = s.get("step", "?")
            action_text = self._sanitize_action_for_summary(s.get('action', 'N/A'))
            md.append(f"[Step {s_idx}]")
            md.append(f"**Raw LLM Response:** {s.get('raw_response', 'N/A')}")
            md.append(f"**Phrased Action to Execute:**\n```bash\n{action_text}\n```")
            r_obs = s.get('observation', 'N/A')
            if len(r_obs) > 2000: 
                r_obs = r_obs[:1999] + "... (raw truncation)"
            md.append(f"**Observation:**\n{r_obs}\n")


        md.append("---")
        
        md.append("## 2. Summarized Trajectory Structure")
        md.append("> **Data Origin**: Derived from LLM summarization of the raw logs. This part focuses on high-level logic and key findings per step.\n")
        
        if isinstance(structure, dict) and "steps" in structure:
            for step in structure["steps"]:
                s_idx = step.get("step_index") or step.get("step", "?")
                raw_action = self._sanitize_action_for_summary(step.get('raw_action', 'N/A'))
                md.append(f"[Summarized Step {s_idx}]")
                md.append(f"**Thought:** {step.get('thought', 'N/A')}")
                md.append(f"**Action:**\n```bash\n{raw_action}\n```")
                
                obs = step.get('observation', 'N/A')
                if len(obs) > 4000: obs = obs[:3999] + "... (truncated)"
                md.append(f"**Observation:**\n{obs}\n")
        else:
            md.append(f"**Warning**: Structured data unavailable. Raw output: {str(structure)}")

        # --- Insert Eureka suggestions ---
        if self.ablation_mode != "no_forensic":
            md.append("---")
            md.append("## 3. Critical Eureka Insights & Optimization Suggestions")
            md.append("> **Data Origin**: Generated by the Eureka-Agent based on the full trajectory analysis.\n")
            md.append(eureka_content)

        return "\n".join(md)
    
    def run_batch_parallel(self, log_dir: Path, max_workers: int = 50):
        log_files = list(Path(log_dir).glob("*.log"))
        self.logger.debug(f"🔥 Loading {len(log_files)} logs...")

        logs_content_raw = {}
        logs_parsed_full = {}
        logs_step_lookup = {}

        for f in log_files:
            try:
                parsed = parse_agent_log(f)
                logs_content_raw[f.name] = parsed["raw_content"]
                logs_parsed_full[f.name] = parsed
                
                logs_step_lookup[f.name] = {s["step"]: s for s in parsed["steps"]}
            except Exception as e:
                self.logger.error(f"Failed to parse {f.name}: {e}")

        all_scores = []
        final_results = defaultdict(dict)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Phase 1: Run summarization for all logs in parallel
            self.logger.debug(f"🚀 Phase 1: Dispatching {len(logs_content_raw)} summarization tasks...")
            structure_futures = {}
            for name, content in logs_content_raw.items():
                parsed_info = logs_parsed_full.get(name, {})
                max_step = len(parsed_info.get("steps", []))
                future = executor.submit(self.summarize_thought_obs, content, max_step=max_step)
                structure_futures[future] = name

            for f in as_completed(structure_futures):
                log_name = structure_futures[f]
                try:
                    result = f.result()
                    step_lookup = logs_step_lookup.get(log_name, {})
                    enriched_data = self._enrich_structure_with_raw(result, step_lookup)
                    final_results[log_name]["structure"] = enriched_data
                    self.logger.debug(f"✅ {log_name} [structure] finished.")
                except LLMDispatcherFatalError:
                    raise
                except Exception as e:
                    self.logger.exception(f"❌ {log_name} [structure] failed: {e}")

            # Phase 2: Run eureka with summarized input (summary + middle 5 raw steps)
            if self.ablation_mode == "no_forensic":
                self.logger.info("⏭️ Ablation B (no_forensic): skipping Phase 2 (eureka diagnosis).")
                for name in logs_content_raw:
                    final_results[name]["eureka"] = {"content": "", "score": 0}
            else:
                self.logger.debug(f"🚀 Phase 2: Dispatching {len(logs_content_raw)} eureka tasks...")
                eureka_futures = {}
                for name in logs_content_raw:
                    structure = final_results[name].get("structure", {})
                    raw_steps = logs_parsed_full.get(name, {}).get("steps", [])
                    eureka_input = self._build_eureka_input(structure, raw_steps)
                    future = executor.submit(self.propose_eureka_idea, eureka_input)
                    eureka_futures[future] = name

                for f in as_completed(eureka_futures):
                    log_name = eureka_futures[f]
                    try:
                        content, score = f.result()
                        final_results[log_name]["eureka"] = {"content": content, "score": score}
                        all_scores.append(score)
                        self.logger.debug(f"✅ {log_name} [eureka] finished.")
                    except LLMDispatcherFatalError:
                        raise
                    except Exception as e:
                        self.logger.exception(f"❌ {log_name} [eureka] failed: {e}")

            # global_actions_data = {}
            # try:
            #     raw_global_data = global_insights_action_future.result()
            #     global_actions_data = self._enrich_global_actions_with_content(raw_global_data, logs_parsed_full)
            #     self.logger.info("🌍 Global action insights finished and enriched.")
            # except Exception as e:
            #     self.logger.error(f"Global action insights failed: {e}")

            # global_thought_data = {}
            # try:
            #     raw_global_data = global_insights_thought_future.result()
            #     global_thought_data = self._enrich_global_actions_with_content(raw_global_data, logs_parsed_full)
            #     self.logger.info("🌍 Global action insights finished and enriched.")
            # except Exception as e:
            #     self.logger.error(f"Global action insights failed: {e}")

        summaries_list = []
        for name, data in final_results.items():
            structure = data.get("structure", {})
            eureka = data.get("eureka", {})
            content = eureka.get("content", "")
            score = eureka.get("score", 0)

            raw_steps = logs_parsed_full.get(name, {}).get("steps", [])
            report_md = self._format_markdown_report(name, structure, content, score, raw_steps)
            (Path(log_dir) / name).with_suffix(".summary.md").write_text(report_md, encoding="utf-8")
            
            with open(Path(log_dir) / f"{name}.analysis.json", "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            summaries_list.append((name, report_md, score))

        avg_score = statistics.mean(all_scores) if all_scores else 0.0
        return {"summaries": summaries_list, "average_score": avg_score}
        # if global_actions_data:
        #     with open(Path(log_dir) / "global_insights.json", "w", encoding="utf-8") as f:
        #         json.dump({"action_insights": global_actions_data}, f, indent=2, ensure_ascii=False)
        #         json.dump({"thought_insights": global_thought_data}, f, indent=2, ensure_ascii=False)
