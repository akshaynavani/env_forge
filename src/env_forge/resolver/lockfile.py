"""Lockfile generation, loading and freshness checks.

``envforge.lock`` is JSON with sorted keys so diffs are reviewable::

    {
      "version": 1,
      "generator": "envforge 0.1.0",
      "manifest_fingerprint": "<sha256>",
      "components": {
        "api": {
          "python": "3.11",
          "packages": {
            "requests": {"version": "2.31.0", "required_by": ["component:api"]},
            ...
          }
        }
      }
    }
"""

import json
import os

from env_forge import __version__
from env_forge.errors import LockfileError, ResolutionError
from env_forge.log import get_logger
from env_forge.resolver.requirements import RequirementGraph, default_marker_env, find_drift
from env_forge.resolver.resolver import Resolver

log = get_logger("lock")

LOCK_NAME = "envforge.lock"
LOCK_VERSION = 1


def lock_path(manifest):
    return os.path.join(manifest.root, LOCK_NAME)


def resolve_component(manifest, component, index, platform="linux"):
    """Resolve one component together with the components it depends on."""
    python = manifest.python_for(component)
    marker_env = default_marker_env(python, platform)
    graph = RequirementGraph()
    for comp in manifest.select([component.name]):
        graph.add_component(comp, manifest.root, marker_env)
    resolver = Resolver(index, marker_env)
    try:
        resolution = resolver.resolve(graph.constraint_map())
    except ResolutionError as exc:
        raise ResolutionError("component '{}': {}".format(component.name, exc))
    return python, resolution


def build_lock(manifest, index, components=None, previous=None, platform="linux"):
    """Resolve the selected components and return a lock dict.

    When ``previous`` is given, components that were not selected are carried over
    unchanged so ``envforge lock api`` does not churn every other team's pins.
    """
    names = components or sorted(manifest.components)
    out = {}
    if previous:
        out.update(previous.get("components", {}))
    for name in names:
        comp = manifest.component(name)
        log.info("resolving %s", name)
        python, res = resolve_component(manifest, comp, index, platform)
        packages = {}
        for pkg in sorted(res.pins):
            entry = {"version": str(res.pins[pkg]),
                     "required_by": res.required_by.get(pkg, [])}
            if res.extras.get(pkg):
                entry["extras"] = res.extras[pkg]
            packages[pkg] = entry
        out[name] = {"python": python, "packages": packages}
        log.info("  %s: %d packages pinned", name, len(packages))
    # drop components that no longer exist in the manifest
    out = {k: v for k, v in out.items() if k in manifest.components}
    return {
        "version": LOCK_VERSION,
        "generator": "envforge " + __version__,
        "manifest_fingerprint": manifest.fingerprint(),
        "components": dict(sorted(out.items())),
    }


def write_lock(path, lock):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(lock, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


def load_lock(path):
    if not os.path.isfile(path):
        raise LockfileError("lockfile not found: {} (run 'envforge lock')".format(path))
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except ValueError as exc:
        raise LockfileError("corrupt lockfile {}: {}".format(path, exc))
    if data.get("version") != LOCK_VERSION:
        raise LockfileError("unsupported lockfile version: {}".format(data.get("version")))
    return data


def is_fresh(manifest, lock):
    return lock.get("manifest_fingerprint") == manifest.fingerprint()


def ensure_fresh(manifest, lock):
    if not is_fresh(manifest, lock):
        raise LockfileError(
            "lockfile is out of date with {} (run 'envforge lock')".format(
                os.path.basename(manifest.path or "envforge.yaml"))
        )
    missing = sorted(set(manifest.components) - set(lock.get("components", {})))
    if missing:
        raise LockfileError("lockfile has no entry for: {}".format(", ".join(missing)))


def pinned_requirements(lock, component_name):
    """Return pip-installable ``name[extras]==version`` lines for one component."""
    try:
        packages = lock["components"][component_name]["packages"]
    except KeyError:
        raise LockfileError("component '{}' is not in the lockfile".format(component_name))
    lines = []
    for name, meta in sorted(packages.items()):
        extras = meta.get("extras") or []
        suffix = "[{}]".format(",".join(extras)) if extras else ""
        lines.append("{}{}=={}".format(name, suffix, meta["version"]))
    return lines


def drift_report(lock):
    """Packages pinned to different versions in different components."""
    return find_drift({
        comp: {pkg: meta["version"] for pkg, meta in body.get("packages", {}).items()}
        for comp, body in lock.get("components", {}).items()
    })
