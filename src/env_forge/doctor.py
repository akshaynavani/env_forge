"""``envforge doctor``: verify a machine is ready to work on the repository.

Used during onboarding (bootstrap.sh runs it) and in CI to fail fast with an
actionable message instead of a cryptic error halfway through provisioning.
"""

import os
import shutil
import sys
from dataclasses import dataclass

from env_forge.errors import EnvForgeError
from env_forge.resolver import lockfile

OK, WARN, FAIL = "ok", "warn", "fail"
MIN_FREE_GB = 2.0


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def _check_host_python():
    v = sys.version_info
    status = OK if v >= (3, 9) else FAIL
    return Check("host python", status, "{}.{}.{} ({})".format(v[0], v[1], v[2], sys.executable))


def _check_tool(runner, tool, required, hint):
    path = runner.which(tool)
    if path:
        return Check(tool, OK, path)
    return Check(tool, FAIL if required else WARN, "not found on PATH; " + hint)


def _check_interpreters(runner, manifest):
    checks = []
    wanted = sorted({manifest.python_for(c) for c in manifest.components.values()
                     if manifest.provisioner_for(c) == "venv"})
    for py in wanted:
        mm = ".".join(py.split(".")[:2])
        path = runner.which("python" + mm)
        checks.append(Check("python" + mm, OK if path else FAIL,
                            path or "needed by venv components; install via pyenv/uv/apt"))
    return checks


def _check_docker(runner, manifest):
    needs = [c.name for c in manifest.components.values()
             if manifest.provisioner_for(c) == "docker"]
    if not needs:
        return []
    if not runner.which("docker"):
        return [Check("docker", FAIL, "required by: " + ", ".join(sorted(needs)))]
    res = runner.run(["docker", "info", "--format", "{{.ServerVersion}}"], check=False, capture=True)
    if not res.ok:
        return [Check("docker daemon", FAIL, "not reachable; start Docker Desktop / dockerd")]
    return [Check("docker daemon", OK, "server " + (res.stdout.strip() or "?"))]


def _check_system_tools(runner, manifest):
    tools = sorted({t for c in manifest.components.values()
                    if manifest.provisioner_for(c) == "venv" for t in c.system})
    return [_check_tool(runner, t, True, "listed under 'system' in envforge.yaml") for t in tools]


def _check_lock(manifest):
    path = lockfile.lock_path(manifest)
    try:
        lock = lockfile.load_lock(path)
        lockfile.ensure_fresh(manifest, lock)
    except EnvForgeError as exc:
        return Check("lockfile", WARN, str(exc))
    return Check("lockfile", OK, "up to date")


def _check_disk(manifest):
    usage = shutil.disk_usage(manifest.root)
    free_gb = usage.free / (1024 ** 3)
    status = OK if free_gb >= MIN_FREE_GB else WARN
    return Check("disk space", status, "{:.1f} GB free".format(free_gb))


def _check_gitignore(manifest):
    gi = os.path.join(manifest.root, ".gitignore")
    if os.path.isfile(gi):
        with open(gi, "r", encoding="utf-8") as fh:
            if any(line.strip().rstrip("/") in (".envforge", "/.envforge") for line in fh):
                return Check(".gitignore", OK, ".envforge/ is ignored")
    return Check(".gitignore", WARN, "add '.envforge/' so local envs are never committed")


def run_checks(manifest, runner):
    checks = [_check_host_python()]
    checks.append(_check_tool(runner, "git", True, "install git"))
    checks += _check_interpreters(runner, manifest)
    checks += _check_docker(runner, manifest)
    checks += _check_system_tools(runner, manifest)
    checks.append(_check_lock(manifest))
    checks.append(_check_disk(manifest))
    checks.append(_check_gitignore(manifest))
    return checks


def summarize(checks):
    """Exit status for the doctor command: 1 if anything failed, else 0."""
    return 1 if any(c.status == FAIL for c in checks) else 0
