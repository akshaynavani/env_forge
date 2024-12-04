"""Docker provisioner: one long-lived container per environment.

The repository root is bind-mounted at ``/workspace`` so tests run against the
working tree, while the interpreter and installed packages live in the container.
"""

import os
import posixpath
import shutil

from env_forge.errors import CommandError, ProvisionError
from env_forge.log import get_logger
from env_forge.provision.base import Provisioner, to_argv
from env_forge.state import EnvRecord

log = get_logger("provision.docker")

CONTAINER_ROOT = "/workspace"


class DockerProvisioner(Provisioner):
    name = "docker"

    def __init__(self, runner, envs_dir, docker_bin=None):
        super().__init__(runner, envs_dir)
        self.docker = docker_bin or os.environ.get("ENVFORGE_DOCKER", "docker")

    # ------------------------------------------------------------- helpers

    def _image(self, spec):
        return spec.image or "python:{}-slim".format(".".join(spec.python.split(".")[:2]))

    def _container_path(self, spec, host_path):
        rel = os.path.relpath(host_path, spec.root)
        if rel.startswith(".."):
            raise ProvisionError("{} is outside the repository root".format(host_path))
        return posixpath.join(CONTAINER_ROOT, *rel.split(os.sep)) if rel != "." else CONTAINER_ROOT

    def _env_flags(self, spec):
        env = dict(spec.env)
        if spec.extra_paths:
            env["PYTHONPATH"] = ":".join(posixpath.join(CONTAINER_ROOT, p) for p in spec.extra_paths)
        flags = []
        for key in sorted(env):
            flags += ["-e", "{}={}".format(key, env[key])]
        return flags

    # ---------------------------------------------------------- primitives

    def preflight(self, spec):
        if not self.runner.which(self.docker):
            raise ProvisionError("'{}' not found on PATH; install Docker or use "
                                 "provisioner: venv".format(self.docker))
        try:
            self.runner.run([self.docker, "info", "--format", "{{.ServerVersion}}"], capture=True)
        except CommandError:
            raise ProvisionError("docker daemon is not reachable (is it running?)")

    def create(self, spec):
        name = "envforge-{}".format(spec.env_id)
        self.runner.run([self.docker, "rm", "-f", name], check=False, capture=True)
        image = self._image(spec)
        cmd = [self.docker, "run", "-d", "--name", name,
               "--label", "envforge.project={}".format(spec.project),
               "--label", "envforge.component={}".format(spec.component),
               "-v", "{}:{}".format(spec.root, CONTAINER_ROOT),
               "-w", self._container_path(spec, spec.workdir)]
        cmd += self._env_flags(spec)
        cmd += [image, "sleep", "infinity"]
        self.runner.run(cmd, capture=True)
        return EnvRecord(env_id=spec.env_id, component=spec.component, provisioner=self.name,
                         location=name, python=spec.python, fingerprint=spec.fingerprint(),
                         status="creating", meta={"image": image})

    def install(self, record, spec):
        pip = [self.docker, "exec", record.location, "python", "-m", "pip",
               "--disable-pip-version-check", "--root-user-action=ignore"]
        self.runner.run(pip + ["install", "--quiet", "--upgrade", "pip"])
        if not spec.requirements:
            return
        # requirements file is written under .envforge/ which is inside the bind mount
        host_dir = os.path.join(self.envs_dir, spec.env_id)
        host_file = os.path.join(host_dir, "requirements.lock.txt")
        if not self.runner.dry_run:
            os.makedirs(host_dir, exist_ok=True)
            with open(host_file, "w", encoding="utf-8") as fh:
                fh.write("\n".join(spec.requirements) + "\n")
        self.runner.run(pip + ["install", "--quiet", "--no-deps", "-r",
                               self._container_path(spec, host_file)])
        for marker in ("pyproject.toml", "setup.py"):
            if os.path.isfile(os.path.join(spec.workdir, marker)):
                self.runner.run(pip + ["install", "--quiet", "--no-deps", "-e",
                                       self._container_path(spec, spec.workdir)])
                break

    def exec(self, record, spec, command, check=True, capture=False):
        cmd = [self.docker, "exec", "-w", self._container_path(spec, spec.workdir)]
        cmd += self._env_flags(spec)
        cmd += [record.location] + to_argv(command)
        return self.runner.run(cmd, check=check, capture=capture)

    def is_alive(self, record):
        res = self.runner.run([self.docker, "inspect", "-f", "{{.State.Running}}", record.location],
                              check=False, capture=True)
        return res.ok and res.stdout.strip() == "true"

    def destroy(self, record):
        self.runner.run([self.docker, "rm", "-f", record.location], check=False, capture=True)
        host_dir = os.path.join(self.envs_dir, record.env_id)
        if os.path.isdir(host_dir) and not self.runner.dry_run:
            shutil.rmtree(host_dir, ignore_errors=True)

    def has_tool(self, record, tool):
        res = self.runner.run([self.docker, "exec", record.location, "sh", "-c",
                               'command -v "$0" >/dev/null', tool], check=False, capture=True)
        return res.ok
