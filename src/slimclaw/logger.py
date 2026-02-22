import os
import sys
import logging

_logger = None


def _setup():
    global _logger
    if _logger is not None:
        return

    import structlog

    level = os.environ.get("LOG_LEVEL", "info").upper()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    _logger = structlog.get_logger()


class _LazyLogger:
    """Proxy that defers structlog import until first log call."""

    __slots__ = ()

    def __getattr__(self, name):
        _setup()
        return getattr(_logger, name)


logger = _LazyLogger()


# Route uncaught errors through structlog so they get timestamps in stderr
def _uncaught_exception_handler(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    _setup()
    _logger.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
    sys.exit(1)


sys.excepthook = _uncaught_exception_handler
