#!/usr/bin/env python3
import os
import json
from random import sample
import yaml
import logging
import argparse
import time
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import copy
from rich.console import Console, Group
from rich.progress import Progress, BarColumn, TextColumn, SpinnerColumn
from rich.panel import Panel
from rich.live import Live
from rich.layout import Layout
from rich.table import Table
from rich.text import Text
from rich.box import ROUNDED, SIMPLE
from rich.style import Style

from langchain_openai import ChatOpenAI
from mini_cyberagent.agent import Agent, PromptTemplates
from mini_cyberagent.command import Command
from common.agent_runtime.docker_env import DockerEnvironment, DockerEnvironmentConfig
from common.agent_runtime.challenge_client import ChallengeClient, ChallengeClientConfig
from common.utils.util import *

def parse_args():
    parser = argparse.ArgumentParser(description="Run CTF agent on challenges.")
    parser.add_argument(
        "--config", "-c", default="mini_cyberagent/configs/mini_ctf.yaml", help="Path to config YAML"
    )
    parser.add_argument(
        "--model", "-m", required=True, help="LLM model name for experiment in common/configs/model.yml"
    )
    parser.add_argument(
        "--run-id", "-r", default="test", help="Run ID for logging"
    )
    parser.add_argument(
        "--sample-time", "-s", type=int, default=1, help="the sample time of one case"
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--challenge-id", "-i", type=str, help="Run a single challenge by ID (e.g., 'pwn_easy')"
    )
    group.add_argument(
        "--benchmark", "-b", type=str, help="Run all challenges in a benchmark (e.g., 'InterCode_CTF' 'cybench' 'nyuctf')"
    )
    group.add_argument(
        "--all", "-a", action="store_true", help="Run all challenges"
    )
    group.add_argument(
        "--challenge-ids", "-ids", nargs='+', type=str,
        help="Run multiple challenges by IDs (e.g., '')"
    )
    parser.add_argument(
        "--category", "-cat", type=str, nargs="*", help="Filter by category (e.g., pwn, rev, web)"
    )
    parser.add_argument(
        "--max-workers", "-w", type=int, default=15, help="Max parallel workers (default: 20)"
    )
    parser.add_argument(
        "--max-challenges", "--max-challenge-num", "-n", type=int, default=None,
        help="Maximum number of challenges to run (after filtering)."
    )
    parser.add_argument(
        "--no-ui", action="store_true", help="Disable rich dashboard UI; use simple progress bar instead."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from existing checkpoint (results.ndjson). Skips already completed tasks."
    )
    parser.add_argument(
        "--retry-failed", action="store_true",
        help="When used with --resume, only retry tasks that previously failed."
    )
    return parser.parse_args()

# ================================
# 1. Extended status model
# ================================
@dataclass
class ChallengeStatus:
    chal_id: str
    benchmark: str
    category: str
    status: str  # "queued", "running", "succeeded", "failed"
    current_step: int = 0
    total_steps: int = 0
    last_command: str = ""
    error: Optional[str] = None
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def duration(self) -> str:
        if self.status == "queued":
            return "-"
        
        start = self.start_time
        end = self.end_time if self.end_time > 0 else time.time()
        if start == 0: return "-"
        
        delta = timedelta(seconds=int(end - start))
        return str(delta)

status_map: Dict[str, ChallengeStatus] = {}

# ================================
# 2. UI core logic (dashboard generator)
# ================================

def generate_dashboard(run_id: str, statuses: List[ChallengeStatus], max_display_rows=15) -> Group:
    """
    Build a combined view: summary stats on top and a sorted task table below.
    """
    total = len(statuses)
    queued = sum(1 for s in statuses if s.status == "queued")
    running = sum(1 for s in statuses if s.status == "running")
    succeeded = sum(1 for s in statuses if s.status == "succeeded")
    failed = sum(1 for s in statuses if s.status == "failed")
    completed = succeeded + failed
    
    # --- A. Top stats row (compact stats) ---
    stats_table = Table.grid(expand=True, padding=(0, 2))
    stats_table.add_column(justify="left", ratio=1)
    stats_table.add_column(justify="right", ratio=1)
    
    # Progress bar logic.
    progress_pct = (completed / total) * 100 if total > 0 else 0
    bar_color = "green" if failed == 0 else "yellow"
    if failed > succeeded: bar_color = "red"
    
    # Left side: run id and current time.
    header_text = Text(f"🚀 CTF Agent | Run: {run_id}", style="bold cyan")
    time_text = Text(datetime.now().strftime("%H:%M:%S"), style="dim white")
    
    # Right side: status count badges.
    badges = [
        Text(f" T:{total} ", style="bold white on black"),
        Text(f" R:{running} ", style="bold black on cyan"),
        Text(f" S:{succeeded} ", style="bold black on green"),
        Text(f" F:{failed} ", style="bold white on red"),
        Text(f" Q:{queued} ", style="dim"),
    ]
    
    stats_table.add_row(
        header_text + Text("  ") + time_text, 
        Text(" ").join(badges)
    )

    # Overall progress bar.
    progress_bar = Progress(
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        BarColumn(bar_width=None, style="dim white", complete_style=bar_color),
        TextColumn("{task.completed}/{task.total}"),
        expand=True
    )
    task_id = progress_bar.add_task("", total=total, completed=completed)
    
    # --- B. Task list (detail table) ---
    # Sort by the most actionable states first: running, failed, succeeded, queued.
    def sort_key(s: ChallengeStatus):
        order = {"running": 0, "failed": 1, "succeeded": 2, "queued": 3}
        return (order.get(s.status, 4), -s.start_time)  # Newer items first within the same status.

    sorted_statuses = sorted(statuses, key=sort_key)
    
    # Limit rows to fit the screen while prioritizing running and failed items.
    display_rows = sorted_statuses[:max_display_rows]

    table = Table(
        expand=True, 
        box=SIMPLE, 
        show_edge=False,
        header_style="bold bright_black",
        padding=(0, 1)
    )

    table.add_column("Stat", width=3, justify="center")
    table.add_column("Challenge / Category", ratio=2)
    table.add_column("Steps", width=8, justify="right")
    table.add_column("Time", width=9, justify="right")
    table.add_column("Last Action / Error", ratio=3, overflow="fold")

    for s in display_rows:
        # 1. Status Icon
        icon_map = {
            "queued": "⚫",
            "running": "🔵",  # A spinner could also be used here.
            "succeeded": "🟢",
            "failed": "🔴"
        }
        icon = icon_map.get(s.status, "?")
        
        # 2. Challenge info: id on the first line, benchmark/category on the second.
        chal_info = Text()
        chal_info.append(s.chal_id, style="bold white")
        chal_info.append(f"\n{s.benchmark}::{s.category}", style="dim bright_black")

        # 3. Steps
        step_style = "cyan" if s.status == "running" else "dim"
        steps = Text(f"{s.current_step}", style=step_style)
        if s.total_steps:
             steps.append(f"/{s.total_steps}", style="dim")
        
        # 4. Time
        time_style = "bold yellow" if s.status == "running" else "dim white"
        duration = Text(s.duration, style=time_style)

        # 5. Last command or error, optimized for dense display.
        cmd_text = Text()
        if s.status == "failed" and s.error:
            cmd_text.append(f"Error: {s.error}", style="bold red")
        elif s.last_command:
            # Remove line breaks and truncate long commands.
            clean_cmd = s.last_command.replace("\n", " ").strip()
            if len(clean_cmd) > 60:
                clean_cmd = clean_cmd[:58] + ".."
            cmd_text.append(f"> {clean_cmd}", style="grey70")
        else:
            cmd_text.append("...", style="dim black")

        table.add_row(icon, chal_info, steps, duration, cmd_text)

    if len(statuses) > max_display_rows:
        hidden_count = len(statuses) - max_display_rows
        table.add_row("", Text(f"... and {hidden_count} more challenges ...", style="italic dim"), "", "", "")

    # Compose the final panel.
    panel_content = Group(
        stats_table,
        Text(""), # Spacer
        progress_bar,
        Text(""), # Spacer
        table
    )
    
    return Panel(panel_content, border_style="grey30", title="Activity Monitor", title_align="left")

def print_execution_summary(console, filtered_chals, args, total_tasks):
    """Print the execution summary panel before running tasks."""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", justify="right")
    grid.add_column(style="white")
    
    grid.add_row("🎯 Target Challenges:", str(len(filtered_chals)))
    grid.add_row("🔄 Samples per Chal:", str(args.sample_time))
    grid.add_row("∑  Total Executions:", f"[bold]{total_tasks}[/]")
    grid.add_row("🧵 Max Workers:", str(args.max_workers))
    grid.add_row("📂 Run id:", args.run_id+"_"+args.model)

    console.print(Panel(
        grid, 
        title="[bold green]CTF Agent Execution Plan[/]", 
        border_style="green",
        expand=False
    ))
    console.print("")  # Spacer line.

def format_result_line(result, sample_current, sample_total):
    """Format a compact single-line result."""
    chal_id = result['chal_id']
    success = result['success']
    duration = result['duration_sec']
    steps = result['attempted_steps']
    
    # Icon and color.
    if success:
        icon = "✅"
        style = "green"
        status_text = "PASS"
    else:
        icon = "❌"
        style = "red"
        status_text = "FAIL"

    # Display id with sample progress, for example: pwn_easy (1/5).
    id_display = f"{chal_id} [dim]({sample_current}/{sample_total})[/]"
    
    # Step display.
    steps_display = f"Steps: {steps}"
    
    # Duration display.
    time_display = f"{duration:>5.1f}s"

    # Error or extra information.
    extra_info = ""
    if not success:
        err = result.get('error', '')
        # Truncate long errors.
        short_err = (err[:40] + '..') if len(err) > 40 else err
        extra_info = f"| [dim red]{short_err}[/]"

    # Compose the output line.
    text = Text()
    text.append(f"{icon} ", style=style)
    text.append(f"{status_text:<4} ", style=f"bold {style}")
    text.append(f"{id_display:<35} ", style="white")
    text.append(f"| {time_display} | {steps_display:<10} {extra_info}")
    
    return text

def run_single_challenge(
    config: Dict[str, Any],
    run_id: str,
    chal_id: str,                   
    challenge_client: ChallengeClient,
    step_limit: int,
    sample_time:int = 1
) -> Dict[str, Any]:
    """
    Runs a single challenge in isolation.
    Returns result dict for main thread to aggregate & update status.
    """
    chal_data = copy.deepcopy(challenge_client.get_challenge_data(chal_id))
    
    status = status_map[chal_id + str(sample_time)]
    status.start_time = time.time()
    try:
        # --- Logging setup (per challenge) ---
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_folder = f"{config['logging']['log_dir']}/{run_id}_{config['agent']['model_kwargs']['model']}_{config['agent']['model_kwargs']['temperature']}_{config['agent']['model_kwargs']['top_p']}/{chal_data['benchmark']}/{chal_data['category']}"
        os.makedirs(log_folder, exist_ok=True)
        log_file = os.path.join(log_folder, f"run_{chal_id}_{sample_time}_{timestamp}.log")
        # Use challenge-specific logger
        logger = logging.getLogger(f"chal.{chal_id}_{sample_time}")
        
        logger.setLevel(config['logging']['level'])
        logger.propagate = False
        logger.handlers.clear()

        fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        fh = logging.FileHandler(log_file, encoding='utf-8')
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        agent_code_path = config["agent"].get("agent_code")
        if agent_code_path:
            logger.info(f"🔁 Importing * from agent code: {agent_code_path}")
            import_star_from_file(agent_code_path, globals())  # inject into global scope of main()
        else:
            logger.warning("⚠️ No 'agent_code' in config; falling back to default mini_cyberagent.agent")
        from mini_cyberagent.agent import Agent, PromptTemplates
        # Console (optional)
        if config['logging']['console_output']:
            ch = logging.StreamHandler(sys.stdout)
            ch.setFormatter(fmt)
            logger.addHandler(ch)
    
        logger.info(f"Starting challenge: {chal_id} (benchmark={chal_data['benchmark']}, category={chal_data['category']})")
        logger.info(f"chal data {chal_data}")
        
        if chal_data["target_status"] == "stopped":
            logger.info(f"Target is stopped, skipping challenge: {chal_id}")
            raise Exception("fail to init target")
        # --- LLM ---
        llm = ChatOpenAI(**config["agent"]["model_kwargs"])

        # --- Environments ---
        docker_config = DockerEnvironmentConfig(**config["docker_environment"])
        attacker_env = DockerEnvironment(config=docker_config, logger=logger)
        
        # --- Setup challenge workspace in attacker env ---
        chal_dir_name = attacker_env._prepare_challenge_files(chal_data)
        chal_data["workspace"] = f"/ctf/{chal_dir_name}"
        logger.info(f'{chal_data["workspace"]} is set in the attacker env')

        # --- Install commands ---
        cmd_dir = f"{chal_data['workspace']}/commands"
        attacker_env.execute(f"mkdir -p {cmd_dir}")
        cmd_docs = ""
        for cmd_path in config["agent"]["command_files"]:
            cmd = Command(cmd_path)
            cmd_docs += cmd.get_prompt_info()
            attacker_env.cp_to_container(cmd.file_path, f"{cmd_dir}/{cmd.name}")
            attacker_env.execute(f"chmod +x {cmd.name}", cwd=cmd_dir)

        logger.info(f'commands loaded in the attacker container')
        success, duration_sec = None, 0.0
        try:

            # --- Agent ---
            prompt_templates = PromptTemplates(
                system_prompt_template=config["agent"]["system_template"],     
                instance_prompt_template=config["agent"]["instance_template"], 
                observation_template=config["agent"]["observation_template"], 
                output_parse_error_template=config["agent"]["output_parse_error_template"], 
            )
            logger.info(f'prompt_templates loaded')
            logger.info(f'agent initialized and the chal data is :\n{chal_data}')

            agent = Agent(
                chal_data=chal_data,
                cmd_docs=cmd_docs,
                prompt_templates=prompt_templates,
                llm=llm,
                env=attacker_env,
                logger=logger
            )

            logger.info(f'agent initialized and the chal data is :\n{chal_data}')
            # --- Hook into agent to report progress ---
            def on_step_callback(step: int, command: str):
                # Thread-safe? Not fully — but GIL + rare update => acceptable for demo
                status.current_step = step + 1
                status.total_steps = step_limit
                status.last_command = command[:50] + "..." if len(command) > 50 else command
                # status.status = "running"
                return status.duration

            agent.on_step_callback = on_step_callback

            # Run!
            status.status = "running"
            success = agent.run(max_steps=step_limit)
            
            status.end_time = time.time()
            status.status = "succeeded" if success else "failed"
            status.total_steps = agent.current_step + 1
            logger.info(f"Challenge {chal_id} finished. Success: {success}")

            duration_sec = status.end_time - status.start_time if status.end_time and status.start_time else 0.0
            if not success:
                if agent.current_step + 1 >= step_limit:
                    status.error = f"Max steps reached ({step_limit})"
                    error_msg = status.error 
                else:
                    # fallback: keep existing error if any, else generic
                    status.error = status.error or "Unknown failure"
                    error_msg = status.error
            else:
                error_msg = None
            
        except Exception as e:
            logger.exception("Critical error during challenge run")
            status.status = "failed"
            status.error = str(e)[:200]
            error_msg = str(e) 
        finally:
            try:
                attacker_env.cleanup()
            except Exception as e:
                logger.warning(f"Cleanup failed for {chal_id}: {e}")

    except Exception as e:
        logger.exception("CRITICAL: Challenge failed with exception")  
        status.status = "failed"
        # Use repr(e) or full traceback for console
        import traceback
        error_msg = "".join(traceback.format_exception_only(type(e), e)).strip()
        status.error = error_msg[:150]
        
        
    return {
        "task_id": f"{chal_id}_{sample_time}", 
        "chal_id": chal_id, 
        "success": success, 
        "error": error_msg,
        "log_file_path": log_file,
        "attempted_steps": agent.current_step+1 if 'agent' in locals() else 0,
        "duration_sec": round(duration_sec, 2),
        "benchmark": chal_data["benchmark"],
        "category": chal_data["category"]
    }


def main():
    args = parse_args()
    console = Console()

    # Load config
    with open(args.config, "r") as f:
        config = yaml.safe_load(f)

    with open("common/configs/model.yml", "r") as f:
        model_config = yaml.safe_load(f)
    config["agent"]["model_kwargs"] |= model_config[args.model]
#     print(config["agent"]["model_kwargs"])
#     from langchain_core.messages import HumanMessage
#     llm = ChatOpenAI(**config["agent"]["model_kwargs"])
#     print(llm.invoke([
#     HumanMessage(content="Briefly describe the main weaknesses of current LLMs under jailbreaking attacks.")
# ]))
#     exit()
    # Setup ChallengeClient
    client_config = ChallengeClientConfig(**config["challenge_client"])
    challenge_client = ChallengeClient(config=client_config)
    all_challenges = challenge_client.challenges  # List[Dict]

    # --- Filter challenges ---
    filtered_chals: Dict[str, Dict[str, Any]] = {}  # {chal_id: chal_data}

    if args.challenge_id:
        if args.challenge_id in all_challenges:
            filtered_chals = {args.challenge_id: all_challenges[args.challenge_id]}
        else:
            console.print(f"[red]❌ Challenge ID '{args.challenge_id}' not found.[/]")
            return

    elif args.challenge_ids:
        missing = []
        for chal_id in args.challenge_ids:
            if chal_id in all_challenges:
                filtered_chals[chal_id] = all_challenges[chal_id]
            else:
                missing.append(chal_id)
        if missing:
            console.print(f"[red]❌ Challenge ID(s) not found: {', '.join(missing)}[/]")
            return
        if not filtered_chals:
            console.print("[red]❌ No valid challenge IDs provided.[/]")
            return
    elif args.benchmark:
        filtered_chals = {
            chal_id: chal_data
            for chal_id, chal_data in all_challenges.items()
            if chal_data.get("benchmark") == args.benchmark
        }

    else:  # --all or no explicit filter
        filtered_chals = all_challenges.copy()  # avoid mutating original

    # Post-filter by category
    if args.category:
        filtered_chals = {
            chal_id: chal_data
            for chal_id, chal_data in filtered_chals.items()
            if chal_data.get("category") in args.category
        }

    if not filtered_chals:
        console.print("[red]❌ No challenges matched the filters.[/]")
        return

    if args.max_challenges is not None and args.max_challenges > 0:
        # Convert to list to preserve order (dict preserves insertion order in Python ≥3.7)
        limited_items = list(filtered_chals.items())[:args.max_challenges]
        filtered_chals = dict(limited_items)
        if len(limited_items) < args.max_challenges:
            console.print(f"[yellow]⚠️  Requested {args.max_challenges} challenges, but only {len(limited_items)} available after filtering.[/]") 
              
    console.print(
        f"[green]✅ Selected {len(filtered_chals)} challenge(s):[/]\n"
        + "\n".join(
            f"  • [cyan]{chal_id}[/] ({chal['benchmark']}/{chal['category']})"
            for chal_id, chal in filtered_chals.items()   # ✅ .items()!
        )
    )


    # --- Rich Progress + Live Display ---
    status_map.clear()
    for chal_id, meta in filtered_chals.items():
        for i in range(1,args.sample_time + 1):
            status_map[chal_id + str(i)] = ChallengeStatus(
                chal_id=chal_id,
                benchmark=meta.get("benchmark", "Unknown"),
                category=meta.get("category", "Unknown"),
                status="queued"
            )
    results = []    
    
    filtered_chals_entries = [(chal_id, sample_time) for sample_time in range(1,args.sample_time + 1) for chal_id in filtered_chals.keys()]
    total_tasks = len(filtered_chals_entries)
    max_workers = min(args.max_workers, len(filtered_chals_entries))

    status_map.clear()
    for chal_id, i in filtered_chals_entries:
        meta = filtered_chals[chal_id]
        status_map[chal_id + str(i)] = ChallengeStatus(
            chal_id=chal_id,
            benchmark=meta.get("benchmark", "Unknown"),
            category=meta.get("category", "Unknown"),
            status="queued"
        )
    
    results = []
    print_execution_summary(console, filtered_chals, args, total_tasks)
    if args.no_ui:
        
        from rich.progress import TimeRemainingColumn
        
        progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.completed}/{task.total}"),
            BarColumn(bar_width=None),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
            transient=False  # Keep the final progress bar state after completion.
        )

        with progress:
            task_id = progress.add_task("Running...", total=total_tasks)
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit tasks.
                futures = {
                    executor.submit(
                        run_single_challenge,
                        config, args.run_id, chal_id, challenge_client, 
                        config["agent"]["step_limit"], sample_time
                    ): (chal_id, sample_time)
                    for chal_id, sample_time in filtered_chals_entries
                }

                # Process results.
                for future in as_completed(futures):
                    chal_id, sample_time = futures[future]
                    result = None
                    try:
                        result = future.result()
                    except Exception as e:
                        # Build a failed result object.
                        result = {
                            "chal_id": chal_id, "success": False, "error": str(e),
                            "attempted_steps": 0, "duration_sec": 0.0,
                            "benchmark": filtered_chals[chal_id].get("benchmark"),
                            "category": filtered_chals[chal_id].get("category")
                        }
                    finally:
                        results.append(result)
                        progress.advance(task_id)
                        
                        # Print above the progress bar so streaming logs do not disrupt it.
                        line = format_result_line(result, sample_current=sample_time, sample_total=args.sample_time)
                        progress.console.print(line)
    else:

        with Live(console=console, refresh_per_second=1, screen=True) as live:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        run_single_challenge,
                        config,
                        args.run_id,
                        chal_id,
                        challenge_client,
                        config["agent"]["step_limit"],
                        sample_time
                    ): (chal_id, sample_time)
                    for chal_id, sample_time in filtered_chals_entries
                }

                # Polling loop
                while not all(f.done() for f in futures):
                    dashboard = generate_dashboard(args.run_id, list(status_map.values()))
                    live.update(dashboard)
                    time.sleep(0.2)

                # Final render
                live.update(generate_dashboard(args.run_id, list(status_map.values())))

                for future in as_completed(futures):
                    chal_id, sample_time = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        console.print(f"[red]⚠️ Worker exception for {chal_id}_{sample_time}: {e}[/]")
                        results.append({
                            "chal_id": chal_id,
                            "sample_time": sample_time,
                            "success": False,
                            "error": str(e),
                            "log_file_path": "",
                            "attempted_steps": 0,
                            "duration_sec": 0,
                            "benchmark": filtered_chals[chal_id]["benchmark"],
                            "category": filtered_chals[chal_id]["category"],
                        })


    # === Generate summary file ===
    summary_data = {
        "run_id": args.run_id,
        "timestamp": datetime.now().isoformat(),
        "config_used": os.path.abspath(args.config),
        "filters": {
            "challenge_id": args.challenge_id,
            "benchmark": args.benchmark,
            "category": args.category,
            "max_challenges": args.max_challenges,
            "sample_time": args.sample_time,
        },
        "stats": {
            "total": len(results),
            "succeeded": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
            "success_rate": round(
                sum(1 for r in results if r["success"]) / len(results) * 100, 2
            ) if results else 0.0,
        },
        "results": results  # already contains all details
    }

    # Write JSON summary
    summary_dir = f"{config['logging']['log_dir']}/{args.run_id}_{config['agent']['model_kwargs']['model']}_{config['agent']['model_kwargs']['temperature']}_{config['agent']['model_kwargs']['top_p']}"
    os.makedirs(summary_dir, exist_ok=True)
    summary_path = os.path.join(summary_dir, "summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    console.print(f"[bold green]✅ Summary saved to:[/] [cyan]{summary_path}[/]")

    # Optional: Markdown summary (for quick human scan)
    md_path = os.path.join(summary_dir, "summary.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# Run Summary: `{args.run_id}`\n\n")
        f.write(f"- **Time**: {summary_data['timestamp']}\n")
        f.write(f"- **Config**: `{args.config}`\n")
        f.write(f"- **Total**: {summary_data['stats']['total']}\n")
        f.write(f"- **Succeeded**: {summary_data['stats']['succeeded']}\n")
        f.write(f"- **Failed**: {summary_data['stats']['failed']}\n")
        f.write(f"- **Success Rate**: {summary_data['stats']['success_rate']:.2f}%\n\n")
        
        if summary_data["stats"]["failed"] > 0:
            f.write("## ❌ Failed Challenges\n\n")
            f.write("| Challenge | Category | Steps | Duration(s) | Log |\n")
            f.write("|-----------|----------|-------|-------------|-----|\n")
            for r in results:
                if not r["success"]:
                    log_link = f"[log]({os.path.relpath(r['log_file_path'], summary_dir)})" if r['log_file_path'] else "—"
                    f.write(f"| `{r['chal_id']}` | `{r['category']}` | {r['attempted_steps']} | {r['duration_sec']} | {log_link} |\n")
        
        f.write("\n## ✅ All Results (JSON)\n\n")
        f.write("See [`summary.json`](summary.json)\n")

    console.print(f"[dim]📄 Markdown summary: {md_path}[/]")

if __name__ == "__main__":
    main()
