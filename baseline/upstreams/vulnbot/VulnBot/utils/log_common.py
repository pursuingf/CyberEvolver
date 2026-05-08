import logging
import os
import time
from enum import Enum

import loguru
import loguru._logger
from memoization import cached, CachingAlgorithmFlag

from config.config import Configs


class RoleType(Enum):
    COLLECTOR = "Collection"
    SCANNER = "Scanning"
    EXPLOITER = "Exploitation"


def _filter_logs(record: dict) -> bool:
    # hide debug logs if Settings.basic_settings.log_verbose=False
    if record["level"].no <= 10 and not Configs.basic_config.log_verbose:
        return False
    # hide traceback logs if Settings.basic_settings.log_verbose=False
    if record["level"].no == 40 and not Configs.basic_config.log_verbose:
        record["exception"] = None
    return True


# 默认每调用一次 build_logger 就会添加一次 hanlder，
@cached(max_size=100, algorithm=CachingAlgorithmFlag.LRU)
def build_logger(log_file: str = "Auto-Pentest"):
    """
    build a logger with colorized output and a log file, for example:

    logger = build_logger("api")
    logger.info("<green>some message</green>")

    user can set basic_settings.log_verbose=True to output debug logs
    use logger.exception to log errors with exceptions
    """
    loguru.logger._core.handlers[0]._filter = _filter_logs
    logger = loguru.logger
    logger.warn = logger.warning
    # logger.error = partial(logger.exception)

    if log_file:
        if not log_file.endswith(".log"):
            log_file = f"{log_file}.log"
        if not os.path.isabs(log_file):
            log_file = str((Configs.basic_config.LOG_PATH / log_file).resolve())
        logger.add(log_file, colorize=False, filter=_filter_logs)

    return logger


class LoggerNameFilter(logging.Filter):
    def filter(self, record):
        return True


def get_timestamp_ms():
    t = time.time()
    return int(round(t * 1000))


def get_log_file(log_path: str, sub_dir: str):
    """
    sub_dir should contain a timestamp.
    """
    log_dir = os.path.join(log_path, sub_dir)
    # Here should be creating a new directory each time, so `exist_ok=False`
    os.makedirs(log_dir, exist_ok=False)
    return os.path.join(log_dir, f"{sub_dir}.log")


def get_config_dict(
        log_level: str, log_file_path: str, log_backup_count: int, log_max_bytes: int
) -> dict:
    # for windows, the path should be a raw string.
    log_file_path = (
        log_file_path.encode("unicode-escape").decode()
        if os.name == "nt"
        else log_file_path
    )
    log_level = log_level.upper()
    config_dict = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "formatter": {
                "format": (
                    "%(asctime)s %(name)-12s %(process)d %(levelname)-8s %(message)s"
                )
            },
        },
        "filters": {
            "logger_name_filter": {
                "()": __name__ + ".LoggerNameFilter",
            },
        },
        "handlers": {
            "stream_handler": {
                "class": "logging.StreamHandler",
                "formatter": "formatter",
                "level": log_level,
            },
            "file_handler": {
                "class": "logging.handlers.RotatingFileHandler",
                "formatter": "formatter",
                "level": log_level,
                "filename": log_file_path,
                "mode": "a",
                "maxBytes": log_max_bytes,
                "backupCount": log_backup_count,
                "encoding": "utf8",
            },
        },
        "loggers": {
            "chatchat_core": {
                "handlers": ["stream_handler", "file_handler"],
                "level": log_level,
                "propagate": False,
            }
        },
        "root": {
            "level": log_level,
            "handlers": ["stream_handler", "file_handler"],
        },
    }
    return config_dict
