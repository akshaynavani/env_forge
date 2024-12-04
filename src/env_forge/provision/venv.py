"""Local virtualenv provisioner (fast, no daemon required)."""

import os
import shutil

from env_forge.errors import ProvisionError
from env_forge.log import get_logger
from env_forge.provision.base import Provisioner, to_argv
from env_forge.state import EnvRecord

log = get_logger("provision.venv")


class VenvProvisioner(Provisioner):
    name = "venv"

    # ------------------------------------------------------------- helpers

    def _interpreter(self, python):
        """Find a host interpreter matching the requested major.minor version."""
        major_minor = ".".join(str(python).split(".")[:2])
        for candidate in ("python{}".format(major_minor), "python3", "python"):
            path = self.runner.which(candidate)
            if not path:
                continue
            if candidate.endswith(major_minor):
                return path
            res = self.runner.run(
                [path, "-c", "import sys;print('%d.%d' % sys.version_info[:2])"],
                check=False, capture=True)
            found = (res.stdout or "").strip()
            if found == major_minor or self.runner.dry_run:
                return path
            log.debug("%s is python %s, want %s", path, found, major_minor)
        raise ProvisionError(
            "no python{} interpreter found on PATH (install it, e.g. via pyenv or "
            "your package manager, or use provisioner: docker)".format(major_minor))

    @staticmethod
    def _bin(location, exe):
        sub = "Scripts" if os.name == "nt" else "bin"
        return os.path.join(location, sub, exe)

    def _env(self, record, spec):
        bindir = os.path.dirname(self._bin(record.location, "python"))
        env = dict(spec.env)
        env["VIRTUAL_ENV"] = record.location
        env["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        extra = [os.path.join(spec.root, p) for p in spec.extra_paths]
        if extra:
            existing = env.get("PYTHONPATH")
            env["PYTHONPATH"] = os.pathsep.join(extra + ([existing] if existing else []))
        env.pop("PYTHONHOME", None)
        return env

    # ---------------------------------------------------------- primitives

    def preflight(self, spec):
        self._interpreter(spec.python)

    def create(self, spec):
        location = os.path.join(self.envs_dir, spec.env_id)
        if os.path.isdir(location) and not self.runner.dry_run:
            shutil.rmtree(location)
        os.makedirs(self.envs_dir, exist_ok=True)
        interpreter = self._interpreter(spec.python)
        self.runner.run([interpreter, "-m", "venv", location])
        return EnvRecord(env_id=spec.env_id, component=spec.component, provisioner=self.name,
                         location=location, python=spec.python, fingerprint=spec.fingerprint(),
                         status="creating", meta={"interpreter": interpreter})

    def install(self, record, spec):
        python = self._bin(record.location, "python")
        pip = [python, "-m", "pip", "--disable-pip-version-check"]
        self.runner.run(pip + ["install", "--quiet", "--upgrade", "pip"])
        if not spec.requirements:
            return
        req_file = os.path.join(record.location, "requirements.lock.txt")
        if not self.runner.dry_run:
            with open(req_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(spec.requirements) + "\n")
        # --no-deps: the lockfile already contains the full transitive closure
        self.runner.run(pip + ["install", "--quiet", "--no-deps", "-r", req_file])
        for marker in ("pyproject.toml", "setup.py"):
            if os.path.isfile(os.path.join(spec.workdir, marker)):
                self.runner.run(pip + ["install", "--quiet", "--no-deps", "-e", spec.workdir])
                break

    def exec(self, record, spec, command, check=True, capture=False):
        argv = to_argv(command)
        return self.runner.run(argv, cwd=spec.workdir, env=self._env(record, spec),
                               check=check, capture=capture)

    def is_alive(self, record):
        return os.path.isfile(self._bin(record.location, "python"))

    def destroy(self, record):
        if self.runner.dry_run:
            log.info("[dry-run] rm -rf %s", record.location)
            return
        shutil.rmtree(record.location, ignore_errors=True)

    def has_tool(self, record, tool):
        # venvs share the host's system tools
        return self.runner.which(tool) is not None
