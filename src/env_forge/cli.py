"""Command-line interface for env_forge."""

import argparse
import json
import sys

from env_forge import __version__
from env_forge import log as logmod
from env_forge.errors import EnvForgeError
from env_forge.manifest import load_manifest

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
