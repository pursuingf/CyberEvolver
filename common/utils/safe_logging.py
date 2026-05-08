from __future__ import annotations

import logging
import sys
import traceback


def _render_message(message: str, args: tuple[object, ...]) -> str:
    if not args:
        return str(message)
    try:
        return str(message) % args
    except Exception:
        rendered_args = ", ".join(repr(arg) for arg in args)
        return f"{message} | args=[{rendered_args}]"


def safe_format_exception(exc: BaseException | None = None) -> str:
    exc = exc or sys.exc_info()[1]
    if exc is None:
        return ""
    try:
        return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).rstrip()
    except BaseException:
        try:
            return "".join(traceback.format_exception_only(type(exc), exc)).rstrip()
        except BaseException:
            return f"{type(exc).__name__}: {exc}"


def safe_log_message(
    logger: logging.Logger,
    level: int,
    message: str,
    *args: object,
) -> None:
    rendered = _render_message(message, args)
    try:
        logger.log(level, message, *args)
        return
    except BaseException as log_exc:
        fallback = (
            f"{rendered} | logging_failure_type={type(log_exc).__name__} "
            f"| logging_failure_message={log_exc}"
        )
        try:
            logger.log(level, fallback)
            return
        except BaseException:
            sys.stderr.write(f"{logging.getLevelName(level)} | {fallback}\n")


def safe_log_exception(
    logger: logging.Logger,
    message: str,
    *args: object,
    exc: BaseException | None = None,
) -> None:
    exc = exc or sys.exc_info()[1]
    try:
        logger.error(message, *args, exc_info=(type(exc), exc, exc.__traceback__) if exc else True)
        return
    except BaseException as log_exc:
        rendered = _render_message(message, args)
        exc_summary = safe_format_exception(exc)
        fallback = (
            f"{rendered} | exception={exc_summary} "
            f"| logging_failure_type={type(log_exc).__name__} "
            f"| logging_failure_message={log_exc}"
        )
        safe_log_message(logger, logging.ERROR, fallback)
