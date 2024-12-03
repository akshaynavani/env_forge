"""Command-line interface for env_forge."""

import argparse
import json
import os
import sys

from env_forge import __version__
from env_forge import log as logmod
from env_forge.errors import EnvForgeError
from env_forge import paths
from env_forge.manifest import load_manifest
from env_forge.resolver import lockfile
from env_forge.resolver.index import build_index

log = logmod.get_logger("cli")


# --------------------------------------------------------------------------- commands


def cmd_validate(args):
    manifest = load_manifest(args.manifest)
    print("ok: {} ({} components)".format(manifest.path, len(manifest.components)))
    return 0


def cmd_show(args):
    manifest = load_manifest(args.manifest)
    comps = manifest.select(args.components)
    if args.json:
        out = {
            "project": manifest.project,
            "python": manifest.python,
            "components": {
                c.name: {
                    "path": c.path,
                    "owner": c.owner,
                    "provisioner": manifest.provisioner_for(c),
                    "python": manifest.python_for(c),
                    "requirements": c.all_requirement_lines(manifest.root),
                    "depends_on": c.depends_on,
                    "test": c.test,
                }
                for c in comps
            },
        }
        print(json.dumps(out, indent=2))
        return 0
    print("project: {}  (python {})".format(manifest.project, manifest.python))
    for c in comps:
        print("  {:<20} {:<8} owner={:<16} path={}".format(
            c.name, manifest.provisioner_for(c), c.owner or "-", c.path))
    return 0


def cmd_lock(args):
    manifest = load_manifest(args.manifest)
    path = lockfile.lock_path(manifest)

    if args.check:
        lock = lockfile.load_lock(path)
        lockfile.ensure_fresh(manifest, lock)
        print("ok: {} is up to date".format(path))
        return 0

    for name in args.components:
        manifest.component(name)  # validate early
    previous = None
    if args.components and os.path.isfile(path):
        previous = lockfile.load_lock(path)

    cache = None if args.no_cache else paths.index_cache_dir()
    index = build_index(manifest.index, manifest.root, cache_dir=cache)
    lock = lockfile.build_lock(manifest, index, args.components, previous, args.platform)
    if args.dry_run:
        print(json.dumps(lock, indent=2, sort_keys=True))
        return 0
    lockfile.write_lock(path, lock)
    print("wrote {}".format(path))
    _print_drift(lock)
    return 0


def _print_drift(lock):
    drift = lockfile.drift_report(lock)
    if not drift:
        return
    print("version drift across components (isolated envs, but worth aligning):")
    for pkg, versions in drift.items():
        detail = "; ".join("{} -> {}".format(v, ",".join(sorted(c))) for v, c in sorted(versions.items()))
        print("  {}: {}".format(pkg, detail))


def cmd_export(args):
    manifest = load_manifest(args.manifest)
    manifest.component(args.component)
    lock = lockfile.load_lock(lockfile.lock_path(manifest))
    lockfile.ensure_fresh(manifest, lock)
    text = "\n".join(lockfile.pinned_requirements(lock, args.component)) + "\n"
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote {}".format(args.output))
    else:
        sys.stdout.write(text)
    return 0


# --------------------------------------------------------------------------- parser


def build_parser():
    parser = argparse.ArgumentParser(
        prog="envforge",
        description="Resolve dependencies, provision isolated test environments and "
                    "generate CI pipelines for multi-component repositories.",
    )
    parser.add_argument("--version", action="version", version="envforge " + __version__)
    parser.add_argument("-f", "--manifest", default=None,
                        help="path to envforge.yaml (default: search upward from cwd)")
    parser.add_argument("-v", "--verbose", action="count", default=0)
    parser.add_argument("-q", "--quiet", action="store_true")
    parser.add_argument("--dry-run", action="store_true",
                        help="print commands instead of executing them")

    sub = parser.add_subparsers(dest="command", metavar="<command>")
    sub.required = True

    p = sub.add_parser("validate", help="validate the manifest")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("show", help="show components defined in the manifest")
    p.add_argument("components", nargs="*")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("lock", help="resolve dependencies and write envforge.lock")
    p.add_argument("components", nargs="*", help="only re-lock these components")
    p.add_argument("--check", action="store_true",
                   help="fail if the lockfile is missing or stale (for CI)")
    p.add_argument("--platform", default="linux", choices=["linux", "darwin", "win32"],
                   help="target platform for environment markers")
    p.add_argument("--no-cache", action="store_true", help="bypass the index cache")
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("export", help="print pinned requirements for a component")
    p.add_argument("component")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_export)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    logmod.configure(args.verbose, args.quiet)
    try:
        return args.func(args) or 0
    except EnvForgeError as exc:
        log.error("%s", exc)
        return exc.exit_code
    except KeyboardInterrupt:
        log.error("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
