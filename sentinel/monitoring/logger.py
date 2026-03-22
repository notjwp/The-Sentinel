import logging
import sys

_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_is_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    global _is_configured
    if _is_configured:
        return

    logging.basicConfig(
        level=level,
        format=_DEFAULT_FORMAT,
        stream=sys.stdout,
    )
    _is_configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
