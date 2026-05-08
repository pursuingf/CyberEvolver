"""Simplified logging utilities for tinker-cookbook."""

import json
import logging
import os
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List

import chz
from rich.console import Console
from rich.table import Table

####### code_state.py ###############

import importlib
import subprocess
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import cast


def code_state(modules: Sequence[str | ModuleType] = ("ttt_discover",)) -> str:
    """
    Return a single diff-formatted string that captures the current code state for the
    provided Python modules. For each module, we:

    - Locate the module on the filesystem
    - Discover the enclosing Git repository (the module may live inside a larger repo)
    - Record the current commit hash (HEAD)
    - Include combined staged+unstaged changes (i.e., diff vs HEAD) for the entire
      containing Git repository (repo-wide). Subtree diffs are omitted to avoid
      duplication.

    The output is suitable for storage alongside experiment or training metadata to
    reproduce the exact code state later. When multiple modules are provided, their
    sections are concatenated in order.

    Parameters:
    - modules: sequence of module import names (e.g., "ttt_discover.rl") or already-
      imported module objects. All entries must be either `str` or `ModuleType`.

    Returns:
    - A string beginning with a header per module of the form:
      "### module: <module_name> (repo: <repo_root>) @ <commit_hash>" followed by
      the staged and unstaged `git diff` outputs restricted to that module directory.
      If a module is not in a Git repository, a short note is included instead.
    """

    def ensure_module(obj: str | ModuleType) -> ModuleType:
        if isinstance(obj, ModuleType):
            return obj
        assert isinstance(obj, str), (
            "Each item in modules must be a module object or import path string"
        )
        return importlib.import_module(obj)

    def find_module_dir(mod: ModuleType) -> Path:
        # Prefer package path if available, else use the file's directory
        mod_file = cast(str | None, getattr(mod, "__file__", None))
        mod_path_list = cast(Sequence[str] | None, getattr(mod, "__path__", None))
        assert (mod_file is not None) or (mod_path_list is not None), (
            f"Module {mod!r} lacks __file__/__path__"
        )
        if mod_path_list is not None:  # packages expose __path__ (iterable); pick the first entry
            first_path = next(iter(mod_path_list))
            return Path(first_path).resolve()
        assert mod_file is not None
        return Path(mod_file).resolve().parent

    def git_toplevel(start_dir: Path) -> Path | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(start_dir), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            )
            return Path(completed.stdout.strip()).resolve()
        except subprocess.CalledProcessError:
            return None

    def git_rev(head_dir: Path) -> str:
        completed = subprocess.run(
            ["git", "-C", str(head_dir), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def git_diff_vs_head(head_dir: Path) -> str:
        """Return a repo-wide unified diff of working tree + index (staged and
        unstaged) relative to HEAD."""
        args = ["git", "-C", str(head_dir), "diff", "--no-color", "HEAD"]
        completed = subprocess.run(args, check=False, capture_output=True, text=True)
        return completed.stdout

    sections: list[str] = []

    # Group modules by their enclosing repo and track non-git modules
    repos_to_modules: dict[Path, list[str]] = {}
    nongit_modules: list[tuple[str, Path]] = []

    for obj in modules:
        mod = ensure_module(obj)
        mod_name = mod.__name__
        mod_dir = find_module_dir(mod)
        repo_root = git_toplevel(mod_dir)

        if repo_root is None:
            nongit_modules.append((mod_name, mod_dir))
            continue

        if repo_root not in repos_to_modules:
            repos_to_modules[repo_root] = []
        if mod_name not in repos_to_modules[repo_root]:
            repos_to_modules[repo_root].append(mod_name)

    # Emit one section per repo with a single repo-wide diff
    for repo_root in sorted(repos_to_modules.keys(), key=lambda p: str(p)):
        try:
            head = git_rev(repo_root)
        except subprocess.CalledProcessError:
            head = "UNKNOWN"

        diff_repo = git_diff_vs_head(repo_root)
        mod_names = ", ".join(sorted(repos_to_modules[repo_root]))
        header = f"### repo: {repo_root} @ {head}\nmodules: {mod_names}\n"
        if diff_repo:
            body = "-- repo-wide (vs HEAD, staged+unstaged) --\n" + diff_repo.rstrip() + "\n"
        else:
            body = "(no local changes)\n"
        sections.append(header + body)

    # Notes for modules not in a git repo
    for mod_name, mod_dir in nongit_modules:
        sections.append(
            f"### module: {mod_name} (not in a git repository)\nmodule_path: {mod_dir}\n"
        )

    return "\n".join(sections)
################################


logger = logging.getLogger(__name__)


# Check WandB availability
_wandb_available = False
try:
    import wandb

    _wandb_available = True
except ImportError:
    wandb = None

# Check Neptune availability
_neptune_available = False
try:
    from neptune_scale import Run as NeptuneRun

    _neptune_available = True
except ImportError:
    NeptuneRun = None

# Check Trackio availability
_trackio_available = False
try:
    import trackio

    _trackio_available = True
except ImportError:
    trackio = None


def dump_config(config: Any) -> Any:
    """Convert configuration object to JSON-serializable format."""
    if hasattr(config, "to_dict"):
        return config.to_dict()
    elif chz.is_chz(config):
        return chz.asdict(config)
    elif is_dataclass(config) and not isinstance(config, type):
        return asdict(config)
    elif isinstance(config, dict):
        return {k: dump_config(v) for k, v in config.items()}
    elif isinstance(config, (list, tuple)):
        return [dump_config(item) for item in config]
    elif isinstance(config, Enum):
        return config.value
    elif hasattr(config, "__dict__"):
        # Handle simple objects with __dict__
        return {
            k: dump_config(v) for k, v in config.__dict__.items() if not k.startswith(("_", "X_"))
        }
    elif callable(config):
        # For callables, return their string representation
        return f"{config.__module__}.{config.__name__}"
    else:
        return config


class Logger(ABC):
    """Abstract base class for loggers."""

    @abstractmethod
    def log_hparams(self, config: Any) -> None:
        """Log hyperparameters/configuration."""
        pass

    @abstractmethod
    def log_metrics(self, metrics: Dict[str, Any], step: int | None = None) -> None:
        """Log metrics dictionary with optional step number."""
        pass

    def log_long_text(self, key: str, text: str) -> None:
        """Log long text content (optional to implement)."""
        pass

    def close(self) -> None:
        """Cleanup when done (optional to implement)."""
        pass

    def sync(self) -> None:
        """Force synchronization (optional to implement)."""
        pass

    def get_logger_url(self) -> str | None:
        """Get a permalink to view this logger's results."""
        return None


class _PermissiveJSONEncoder(json.JSONEncoder):
    """A JSON encoder that handles non-encodable objects by converting them to their type string."""

    def default(self, o: Any) -> Any:
        try:
            return super().default(o)
        except TypeError:
            # Only handle the truly non-encodable objects
            return str(type(o))


class JsonLogger(Logger):
    """Logger that writes metrics to a JSONL file."""

    def __init__(self, log_dir: str | Path):
        self.log_dir = Path(log_dir).expanduser()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_file = self.log_dir / "metrics.jsonl"
        self._logged_hparams = False

    def log_hparams(self, config: Any) -> None:
        """Log hyperparameters to a separate config.json file."""
        if not self._logged_hparams:
            config_dict = dump_config(config)
            config_file = self.log_dir / "config.json"
            with open(config_file, "w") as f:
                json.dump(config_dict, f, indent=2, cls=_PermissiveJSONEncoder)
            diff_file = code_state()
            with open(self.log_dir / "code.diff", "w") as f:
                f.write(diff_file)
            self._logged_hparams = True

    def log_metrics(self, metrics: Dict[str, Any], step: int | None = None) -> None:
        """Append metrics to JSONL file."""
        log_entry = {"step": step} if step is not None else {}
        log_entry.update(metrics)

        with open(self.metrics_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")
            logger.info("Wrote metrics to %s", self.metrics_file)


class PrettyPrintLogger(Logger):
    """Logger that displays metrics in a formatted table in the console."""

    def __init__(self):
        self.console = Console()
        self._last_step = None

    def log_hparams(self, config: Any) -> None:
        """Print configuration summary."""
        config_dict = dump_config(config)
        with _rich_console_use_logger(self.console):
            self.console.print("[bold cyan]Configuration:[/bold cyan]")
            for key, value in config_dict.items():
                self.console.print(f"  {key}: {_maybe_truncate_repr(value)}")

    def log_metrics(self, metrics: Dict[str, Any], step: int | None = None) -> None:
        """Display metrics in console."""
        if not metrics:
            return

        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("Metric", style="cyan", width=30)
        table.add_column("Value", style="green")

        if step is not None:
            table.title = f"Step {step}"

        for key, value in sorted(metrics.items()):
            if isinstance(value, float):
                value_str = f"{value:.6f}"
            else:
                value_str = str(value)
            table.add_row(key, value_str)

        with _rich_console_use_logger(self.console):
            self.console.print(table)


def _maybe_truncate_repr(value: Any) -> str:
    repr_value = repr(value)
    if len(repr_value) > 256:
        return repr_value[:128] + " ... " + repr_value[-128:]
    return repr_value


@contextmanager
def _rich_console_use_logger(console: Console):
    with console.capture() as capture:
        yield
    logger.info("\n" + capture.get().rstrip())
    # ^^^ add a leading newline so things like table formatting work properly


class WandbLogger(Logger):
    """Logger for Weights & Biases."""

    def __init__(
        self,
        project: str | None = None,
        config: Any | None = None,
        log_dir: str | Path | None = None,
        wandb_name: str | None = None,
        run_id: str | None = None,
    ):
        if not _wandb_available:
            raise ImportError(
                "wandb is not installed. Please install it with: "
                "pip install wandb (or uv add wandb)"
            )

        if not os.environ.get("WANDB_API_KEY"):
            raise ValueError("WANDB_API_KEY environment variable not set")

        # Initialize wandb run
        assert wandb is not None  # For type checker
        resume_mode = "allow" if run_id is not None else "never"
        self.run = wandb.init(
            project=project,
            config=dump_config(config) if config else None,
            dir=str(log_dir) if log_dir else None,
            name=wandb_name,
            id=run_id,
            resume=resume_mode
        )

    def log_hparams(self, config: Any) -> None:
        """Log hyperparameters to wandb."""
        if self.run and wandb is not None:
            wandb.config.update(dump_config(config))

    def log_metrics(self, metrics: Dict[str, Any], step: int | None = None) -> None:
        """Log metrics to wandb."""
        if self.run and wandb is not None:
            wandb.log(metrics, step=step)
            logger.info("Logging to: %s", self.run.url)

    def close(self) -> None:
        """Close wandb run."""
        if self.run and wandb is not None:
            wandb.finish()

    def get_logger_url(self) -> str | None:
        """Get the URL of the wandb run."""
        if self.run and wandb is not None:
            return self.run.url
        return None

class MultiplexLogger(Logger):
    """Logger that forwards operations to multiple child loggers."""

    def __init__(self, loggers: List[Logger]):
        self.loggers = loggers

    def log_hparams(self, config: Any) -> None:
        """Forward log_hparams to all child loggers."""
        for logger in self.loggers:
            logger.log_hparams(config)

    def log_metrics(self, metrics: Dict[str, Any], step: int | None = None) -> None:
        """Forward log_metrics to all child loggers."""
        for logger in self.loggers:
            logger.log_metrics(metrics, step)

    def log_long_text(self, key: str, text: str) -> None:
        """Forward log_long_text to all child loggers."""
        for logger in self.loggers:
            if hasattr(logger, "log_long_text"):
                logger.log_long_text(key, text)

    def close(self) -> None:
        """Close all child loggers."""
        for logger in self.loggers:
            if hasattr(logger, "close"):
                logger.close()

    def sync(self) -> None:
        """Sync all child loggers."""
        for logger in self.loggers:
            if hasattr(logger, "sync"):
                logger.sync()

    def get_logger_url(self) -> str | None:
        """Get the first URL returned by the child loggers."""
        for logger in self.loggers:
            if url := logger.get_logger_url():
                return url
        return None


def initialize_or_resume_wandb_logger(wandb_project, config, log_dir, wandb_name):
    """
    Initialize a WandbLogger, resuming the latest run with the same name if it exists.

    Assumes:
      - WANDB_ENTITY and WANDB_API_KEY are set in the environment.
      - WandbLogger forwards unknown kwargs to `wandb.init` (for id/resume).
    """
    api = wandb.Api()

    # Entity: prefer env var, otherwise fall back to W&B's default entity
    entity = os.environ.get("WANDB_ENTITY", None)
    if entity is None:
        entity = api.default_entity  # uses logged-in user/team

    project_path = f"{entity}/{wandb_project}"

    # Filter by run "Name" (API key is `display_name`)
    runs = api.runs(project_path, filters={"display_name": wandb_name})

    latest_run_id = None
    latest_created_at = None

    # Find the most recent run with this name
    for run in runs:
        if latest_created_at is None or run.created_at > latest_created_at:
            latest_created_at = run.created_at
            latest_run_id = run.id

    # Construct your logger – assumes WandbLogger is already imported / defined
    logger = WandbLogger(
        project=wandb_project,
        config=config,
        log_dir=log_dir,
        wandb_name=wandb_name,
        run_id=latest_run_id
    )

    return logger


def setup_logging(
    log_dir: str,
    wandb_project: str | None = None,
    wandb_name: str | None = None,
    config: Any | None = None,
    do_configure_logging_module: bool = True,
) -> Logger:
    """
    Set up logging infrastructure with multiple backends.

    Args:
        log_dir: Directory for logs
        wandb_project: W&B project name (if None, W&B logging is skipped)
        wandb_name: W&B run name
        config: Configuration object to log
        do_configure_logging_module: Whether to configure the logging module

    Returns:
        MultiplexLogger that combines all enabled loggers
    """
    # Create log directory
    log_dir_path = Path(log_dir).expanduser()
    log_dir_path.mkdir(parents=True, exist_ok=True)

    # Initialize loggers
    loggers = []

    # Always add JSON logger
    loggers.append(JsonLogger(log_dir_path))

    # Always add pretty print logger
    loggers.append(PrettyPrintLogger())

    # Add W&B logger if available and configured
    if wandb_project:
        if not _wandb_available:
            print("WARNING: wandb is not installed. Skipping W&B logging.")
        elif not os.environ.get("WANDB_API_KEY"):
            print("WARNING: WANDB_API_KEY environment variable not set. Skipping W&B logging. ")
        else:
            loggers.append(
                initialize_or_resume_wandb_logger(
                    wandb_project, 
                    config, 
                    log_dir, 
                    wandb_name
                )
            )

    # Create multiplex logger
    ml_logger = MultiplexLogger(loggers)

    # Log initial configuration
    if config is not None:
        ml_logger.log_hparams(config)

    if do_configure_logging_module:
        configure_logging_module(str(log_dir_path / "logs.log"))

    logger.info(f"Logging to: {log_dir_path}")
    return ml_logger


def configure_logging_module(path: str, level: int = logging.INFO) -> logging.Logger:
    """Configure logging to console (color) and file (plain), forcing override of prior config."""
    # ANSI escape codes for colors
    COLORS = {
        "DEBUG": "\033[94m",  # Blue
        "INFO": "\033[92m",  # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",  # Red
        "CRITICAL": "\033[95m",  # Magenta
    }
    RESET = "\033[0m"

    class ColorFormatter(logging.Formatter):
        """Colorized log formatter for console output that doesn't mutate record.levelname."""

        def format(self, record: logging.LogRecord) -> str:
            color = COLORS.get(record.levelname, "")
            # add a separate attribute for the colored level name
            record.levelname_colored = f"{color}{record.levelname}{RESET}"
            return super().format(record)

    class AsyncioSocketWarningFilter(logging.Filter):
        """Filter to suppress asyncio socket.send() warnings."""
        def filter(self, record: logging.LogRecord) -> bool:
            # Suppress asyncio warnings about socket.send() exceptions
            if record.name == "asyncio" and "socket.send() raised exception" in record.getMessage():
                return False
            return True

    # Console handler with colors
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        ColorFormatter("%(name)s:%(lineno)d [%(levelname_colored)s] %(message)s")
    )
    console_handler.addFilter(AsyncioSocketWarningFilter())

    # File handler without colors
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(name)s:%(lineno)d [%(levelname)s] %(message)s"))
    file_handler.addFilter(AsyncioSocketWarningFilter())

    # Suppress asyncio warnings at the logger level as well
    asyncio_logger = logging.getLogger("asyncio")
    asyncio_logger.setLevel(logging.ERROR)

    # Force override like basicConfig(..., force=True)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    return root
