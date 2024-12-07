"""Command-line interface for env_forge."""

import argparse
import json
import os
import sys

from env_forge import __version__
from env_forge import log as logmod
from env_forge.errors import EnvForgeError
from env_forge import paths
from env_forge import doctor
from env_forge.ci import GENERATORS, get_generator
from env_forge.ci.affected import affected_components, changed_files
from env_forge.forge import EnvForge
from env_forge.manifest import load_manifest
from env_forge.runner import Runner
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


def _forge(args, manifest):
    runner = Runner(dry_run=args.dry_run)
    if getattr(args, "relock", False):
        path = lockfile.lock_path(manifest)
        lock = lockfile.load_lock(path) if os.path.isfile(path) else None
        if lock is None or not lockfile.is_fresh(manifest, lock):
            log.info("lockfile missing or stale; re-resolving")
            index = build_index(manifest.index, manifest.root, cache_dir=paths.index_cache_dir())
            lock = lockfile.build_lock(manifest, index)
            if not args.dry_run:
                lockfile.write_lock(path, lock)
        return EnvForge(manifest, runner, lock=lock)
    return EnvForge(manifest, runner)


def cmd_up(args):
    manifest = load_manifest(args.manifest)
    forge = _forge(args, manifest)
    names = args.components or sorted(manifest.components)
    for name in names:
        rec = forge.up(name, recreate=args.recreate)
        print("{:<20} {:<8} {}".format(name, rec.provisioner, rec.location))
    return 0


def cmd_down(args):
    manifest = load_manifest(args.manifest)
    forge = EnvForge(manifest, Runner(dry_run=args.dry_run), lock={}, require_fresh_lock=False)
    if not args.all and not args.components:
        raise EnvForgeError("name a component or pass --all")
    removed = []
    if args.all:
        removed = forge.down(all_envs=True)
    else:
        for name in args.components:
            removed += forge.down(name)
    print("removed {} environment(s)".format(len(removed)))
    return 0


def cmd_list(args):
    manifest = load_manifest(args.manifest)
    forge = EnvForge(manifest, Runner(), lock={}, require_fresh_lock=False)
    records = forge.state.all()
    if args.json:
        print(json.dumps([r.__dict__ for r in records], indent=2))
        return 0
    if not records:
        print("no environments provisioned")
        return 0
    print("{:<16} {:<8} {:<8} {:<7} {}".format("COMPONENT", "BACKEND", "PYTHON", "STATUS", "ENV"))
    for r in records:
        print("{:<16} {:<8} {:<8} {:<7} {}".format(
            r.component, r.provisioner, r.python, r.status, r.env_id))
    return 0


def cmd_run(args):
    manifest = load_manifest(args.manifest)
    forge = _forge(args, manifest)
    command = args.cmd
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise EnvForgeError("no command given (usage: envforge run <component> -- <cmd>)")
    return forge.run(args.component, command).returncode


def cmd_test(args):
    manifest = load_manifest(args.manifest)
    forge = _forge(args, manifest)
    outcomes = forge.test(args.components, ephemeral=args.ephemeral, fail_fast=args.fail_fast)
    failed = [o for o in outcomes if not o.passed]
    print()
    for o in outcomes:
        status = "PASS" if o.passed else "FAIL"
        extra = " ({})".format(o.error) if o.error else ""
        print("  {:<4} {:<20} {:6.1f}s{}".format(status, o.component, o.duration, extra))
    print("\n{} passed, {} failed".format(len(outcomes) - len(failed), len(failed)))
    return 1 if failed else 0


def cmd_ci_generate(args):
    manifest = load_manifest(args.manifest)
    providers = args.provider or manifest.ci.get("providers") or ["github"]
    if isinstance(providers, str):
        providers = [providers]
    for name in providers:
        gen = get_generator(name, manifest)
        text = gen.render()
        if args.stdout or args.dry_run:
            sys.stdout.write(text)
            continue
        out = gen.output_path()
        if os.path.isfile(out) and not args.force:
            with open(out, "r", encoding="utf-8") as fh:
                if fh.read() == text:
                    print("unchanged {}".format(out))
                    continue
            if not args.check:
                raise EnvForgeError("{} exists and differs; pass --force to overwrite".format(out))
        if args.check:
            # CI usage: fail if the committed pipeline drifted from the manifest
            current = open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
            if current != text:
                raise EnvForgeError("{} is out of date; run 'envforge ci generate --force'".format(out))
            continue
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("wrote {}".format(out))
    return 0


def cmd_affected(args):
    manifest = load_manifest(args.manifest)
    files = args.files or changed_files(Runner(), manifest.root, args.base, args.head)
    names = affected_components(manifest, files)
    if args.json:
        print(json.dumps(names))
    else:
        print("\n".join(names))
    return 0


def cmd_doctor(args):
    manifest = load_manifest(args.manifest)
    checks = doctor.run_checks(manifest, Runner())
    marks = {doctor.OK: "[ok]  ", doctor.WARN: "[warn]", doctor.FAIL: "[FAIL]"}
    for c in checks:
        print("{} {:<16} {}".format(marks[c.status], c.name, c.detail))
    code = doctor.summarize(checks)
    print("\n" + ("ready to go" if code == 0 else "fix the failures above, then re-run"))
    return code


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

    p = sub.add_parser("up", help="provision isolated environments (one command)")
    p.add_argument("components", nargs="*", help="default: all components")
    p.add_argument("--recreate", action="store_true", help="rebuild even if up to date")
    p.add_argument("--relock", action="store_true", help="re-resolve first if the lock is stale")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="destroy provisioned environments")
    p.add_argument("components", nargs="*")
    p.add_argument("--all", action="store_true")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("list", help="list provisioned environments")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("run", help="run a command inside a component's environment")
    p.add_argument("component")
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p.add_argument("--relock", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("test", help="provision and run each component's test command")
    p.add_argument("components", nargs="*", help="default: all components")
    p.add_argument("--ephemeral", action="store_true", help="destroy envs after testing (CI)")
    p.add_argument("--fail-fast", action="store_true")
    p.add_argument("--relock", action="store_true")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("ci", help="CI/CD integration")
    ci_sub = p.add_subparsers(dest="ci_command", metavar="<ci-command>")
    ci_sub.required = True
    g = ci_sub.add_parser("generate", help="generate CI pipeline files from the manifest")
    g.add_argument("--provider", action="append", choices=sorted(GENERATORS),
                   help="repeatable; default: ci.providers from the manifest")
    g.add_argument("--stdout", action="store_true", help="print instead of writing files")
    g.add_argument("--force", action="store_true", help="overwrite existing files")
    g.add_argument("--check", action="store_true", help="fail if committed files are stale")
    g.set_defaults(func=cmd_ci_generate)

    p = sub.add_parser("affected", help="list components affected by changes since a git ref")
    p.add_argument("--base", default="origin/main")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--json", action="store_true")
    p.add_argument("files", nargs="*", help="explicit changed files (skips git diff)")
    p.set_defaults(func=cmd_affected)

    p = sub.add_parser("doctor", help="check this machine is ready to work on the repo")
    p.set_defaults(func=cmd_doctor)

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
