"""Filesystem locations used by env_forge.

Per-project state lives under ``<repo>/.envforge`` (gitignored). Shared caches live
under ``$ENVFORGE_HOME`` (default ``$XDG_CACHE_HOME/envforge`` or ``~/.cache/envforge``).
"""

import os

PROJECT_DIR = ".envforge"


def home():
    explicit = os.environ.get("ENVFORGE_HOME")
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "envforge")


def index_cache_dir():
    return os.path.join(home(), "index")


def project_dir(manifest):
    return os.path.join(manifest.root, PROJECT_DIR)


def envs_dir(manifest):
    return os.path.join(project_dir(manifest), "envs")


def state_file(manifest):
    return os.path.join(project_dir(manifest), "state.json")
