"""Small logging setup shared by all modules."""

import logging
import os
import sys

LOGGER_NAME = "env_forge"


def get_logger(name=None):
    if name:
        return logging.getLogger("{}.{}".format(LOGGER_NAME, name))
    return logging.getLogger(LOGGER_NAME)


def configure(verbosity=0, quiet=False):
    """Configure root env_forge logger. verbosity: 0=INFO, 1+=DEBUG."""
    if quiet:
        level = logging.WARNING
    elif verbosity > 0 or os.environ.get("ENVFORGE_DEBUG"):
        level = logging.DEBUG
    else:
        level = logging.INFO

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        fmt = "%(levelname).1s %(message)s" if level > logging.DEBUG else \
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
        logger.addHandler(handler)
    logger.propagate = False
    return logger
