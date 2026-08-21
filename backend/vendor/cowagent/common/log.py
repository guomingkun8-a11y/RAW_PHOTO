import logging
import os
import sys
import io


def _log_path():
    # Mirror config.get_data_root() without importing config (avoids a circular
    # import, since config imports this module). COW_DATA_DIR relocates logs;
    # source deployments fall back to CWD.
    data_dir = os.environ.get("COW_DATA_DIR")
    if data_dir:
        data_dir = os.path.expanduser(data_dir)
        os.makedirs(data_dir, exist_ok=True)
        return os.path.join(data_dir, "run.log")
    return "run.log"


def _reset_logger(log):
    for handler in log.handlers:
        handler.close()
        log.removeHandler(handler)
        del handler
    log.handlers.clear()
    log.propagate = False
    stdout = sys.stdout
    if hasattr(stdout, "buffer"):
        stdout = io.TextIOWrapper(stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    console_handle = logging.StreamHandler(stdout)
    console_handle.setFormatter(
        logging.Formatter(
            "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    log.addHandler(console_handle)
    # File logging is best-effort: if the log path isn't writable (e.g. a
    # packaged app installed under Program Files run by a non-admin user, with
    # an unwritable CWD), fall back to console-only instead of crashing the
    # whole process at import time.
    try:
        file_handle = logging.FileHandler(_log_path(), encoding="utf-8")
        file_handle.setFormatter(
            logging.Formatter(
                "[%(levelname)s][%(asctime)s][%(filename)s:%(lineno)d] - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        log.addHandler(file_handle)
    except OSError:
        console_handle.handle(
            logging.LogRecord(
                "log", logging.WARNING, __file__, 0,
                "[log] file logging disabled (log path not writable): %s",
                (_log_path(),), None,
            )
        )


def _get_logger():
    log = logging.getLogger("log")
    _reset_logger(log)
    log.setLevel(logging.INFO)
    return log


# 日志句柄
logger = _get_logger()
