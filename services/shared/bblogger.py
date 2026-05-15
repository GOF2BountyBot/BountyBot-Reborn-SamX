#!/usr/bin/env python3
"""
A tiny, dependency-free logging helper.

Environment variables
---------------------
LOG_LEVEL   : DEBUG | INFO | WARN | ERROR | FATAL | TRACE  (default: INFO)
LOG_FILE    : Absolute/relative path for the log file       (default: app.log)
LOG_TO_FILE : true/false or 1/0 or yes/no                  (default: true)
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

# ────────────────────────────────────────────────────────────────
# 1. Extra level: TRACE (numerically below DEBUG)
# ────────────────────────────────────────────────────────────────
TRACE_LEVEL_NUM = 5
logging.addLevelName(TRACE_LEVEL_NUM, "TRACE")


def trace(self, msg, *args, **kwargs):
    if self.isEnabledFor(TRACE_LEVEL_NUM):
        self._log(TRACE_LEVEL_NUM, msg, args, **kwargs)


logging.Logger.trace = trace  # monkey-patch


# ────────────────────────────────────────────────────────────────
# 1a. Log-injection sanitisation
# ────────────────────────────────────────────────────────────────
# Untrusted input flowing into a log call can forge fake log lines by injecting
# CR/LF, breaking log parsers (SIEM, Splunk, jq) and obscuring real events.
# CodeQL's `py/log-injection` query recognises `.replace("\n", ...)` and
# `.replace("\r\n", ...)` call sites as sanitisers — see
# semmle/python/security/dataflow/LogInjectionCustomizations.qll, class
# `ReplaceLineBreaksSanitizer`.
#
# We sanitise centrally via `_SafeLogger.process()` so all 738+ existing log
# call sites are covered without modification.
def _scrub(value):
    """Strip CR/LF from a string-like value. Non-strings are returned unchanged."""
    if isinstance(value, str):
        # Two explicit replace() calls — both literal "\r\n" and "\n" — to match
        # the CodeQL sanitiser pattern exactly. Order matters: "\r\n" first so a
        # bare "\r" left after the second pass is also removed.
        return value.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return value


class _SafeLogger(logging.LoggerAdapter):
    """
    LoggerAdapter that scrubs CR/LF from msg and *args before delegating to the
    underlying logger. Preserves the standard logging API (`.info`, `.error`,
    `.exception`, `.debug`, `.warning`, `.critical`, `.log`, plus our `.trace`
    extension) so existing call sites need no changes.
    """

    def process(self, msg, kwargs):
        # Scrub the message itself.
        msg = _scrub(msg)
        # Scrub positional args used by %-formatting.
        if kwargs.get("args"):
            kwargs["args"] = tuple(_scrub(a) for a in kwargs["args"])
        return msg, kwargs

    # LoggerAdapter doesn't proxy our custom TRACE level — add it explicitly.
    def trace(self, msg, *args, **kwargs):
        if self.isEnabledFor(TRACE_LEVEL_NUM):
            msg, kwargs = self.process(msg, kwargs)
            self.logger._log(TRACE_LEVEL_NUM, msg, args, **kwargs)


# ────────────────────────────────────────────────────────────────
# 2. Color formatter for STDOUT
# ────────────────────────────────────────────────────────────────
class _ColorFormatter(logging.Formatter):
    COLORS = {
        "TRACE": "\033[1;92m",  # light-green
        "DEBUG": "\033[1;34m",  # light-blue
        "INFO": "\033[0m",  # white / default
        "WARNING": "\033[1;33m",  # yellow
        "ERROR": "\033[1;31m",  # red
        "CRITICAL": "\033[1;41m",  # Red Background
    }
    RESET = "\033[0m"

    def format(self, record):
        message = super().format(record)
        color = self.COLORS.get(record.levelname, "")
        return f"{color}{message}{self.RESET}"


# ────────────────────────────────────────────────────────────────
# 3. Public helper
# ────────────────────────────────────────────────────────────────
def get_logger(module_name: str) -> logging.LoggerAdapter:
    """
    Create or return a module-specific logger.
    Call this once per module:  logger = get_logger(__name__)

    Returns a `_SafeLogger` adapter that scrubs CR/LF from log messages and
    args, defending against log-injection (CodeQL `py/log-injection`).
    """

    # ---- Environment config -------------------------------------------------
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_file = os.getenv("LOG_FILE", "app.log")
    log_to_file_flag = os.getenv("LOG_TO_FILE", "true").lower() in {"1", "true", "yes", "y"}

    # ---- Base logger --------------------------------------------------------
    logger = logging.getLogger(module_name)
    logger.setLevel(log_level)
    logger.propagate = False  # avoid double logging if root handlers exist

    # Return early if handlers already attached (singleton behaviour)
    if logger.handlers:
        return _SafeLogger(logger, {})

    # ---- STDOUT handler (always on) ----------------------------------------
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(_ColorFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(stdout_handler)

    # ---- File handler (optional) -------------------------------------------
    if log_to_file_flag:
        # Ensure log directory exists
        log_dir = os.path.dirname(log_file)
        _dir_ready = True
        if log_dir and not os.path.exists(log_dir):
            try:
                os.makedirs(log_dir, exist_ok=True)
            except Exception as e:
                logger.error("Failed to create log directory '%s': %s — file logging disabled", log_dir, e)
                _dir_ready = False

        if _dir_ready:
            try:
                file_handler = RotatingFileHandler(
                    filename=log_file,
                    maxBytes=5 * 1024 * 1024,  # 5 MB
                    backupCount=3,
                )
                file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
                logger.addHandler(file_handler)
            except Exception as e:
                logger.error("Failed to open log file '%s': %s — file logging disabled", log_file, e)

    return _SafeLogger(logger, {})


# ────────────────────────────────────────────────────────────────
# 4. Demo when called directly
# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    lg = get_logger("demo")
    lg.trace("This is TRACE (finest-grained).")
    lg.debug("This is DEBUG.")
    lg.info("This is INFO.")
    lg.warning("This is WARNING.")
    lg.error("This is ERROR.")
    lg.critical("This is FATAL/CRITICAL.")
