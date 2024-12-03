"""Provisioner abstraction.

A provisioner turns an ``EnvSpec`` (what the environment must contain) into a running,
isolated environment and can execute commands inside it. ``up()`` is a template method:
subclasses implement the primitive steps, the base class fixes the order.
"""

import abc
import hashlib
import json
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from env_forge.errors import ProvisionError
from env_forge.log import get_logger
from env_forge.state import EnvRecord

log = get_logger("provision")

_SHELL_CHARS = re.compile(r"[|&;<>()$`*?~\n]")


def to_argv(command):
    """Turn a manifest command string into argv.

    Plain commands are split with shlex (no shell). Commands that use shell syntax
    (pipes, &&, redirects, globs, variables) are run through ``sh -c`` explicitly,
    since they come from the checked-in manifest, exactly like CI ``run:`` steps.
    """
    if isinstance(command, (list, tuple)):
        return [str(c) for c in command]
    if _SHELL_CHARS.search(command):
        return ["sh", "-c", command]
    return shlex.split(command)


@dataclass
class EnvSpec:
    project: str
    component: str
    python: str
    requirements: List[str]
    workdir: str                       # absolute path of the component on the host
    root: str                          # repository root on the host
    env: Dict[str, str] = field(default_factory=dict)
    setup: List[str] = field(default_factory=list)
    test: str = "pytest -q"
    system: List[str] = field(default_factory=list)
    image: Optional[str] = None
    extra_paths: List[str] = field(default_factory=list)  # repo-relative paths for PYTHONPATH

    def fingerprint(self):
        blob = json.dumps({"python": self.python, "requirements": sorted(self.requirements),
                           "image": self.image}, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()

    @property
    def env_id(self):
        return "{}-{}-{}".format(self.project, self.component, self.fingerprint()[:10])


class Provisioner(abc.ABC):
    name = "abstract"

    def __init__(self, runner, envs_dir):
        self.runner = runner
        self.envs_dir = envs_dir

    # ----------------------------------------------------------- primitives

    @abc.abstractmethod
    def preflight(self, spec):
        """Check host prerequisites (interpreter, docker daemon...). Raise ProvisionError."""

    @abc.abstractmethod
    def create(self, spec):
        """Create an empty isolated environment and return its EnvRecord."""

    @abc.abstractmethod
    def install(self, record, spec):
        """Install the pinned requirements into the environment."""

    @abc.abstractmethod
    def exec(self, record, spec, command, check=True, capture=False):
        """Run ``command`` inside the environment, from the component directory."""

    @abc.abstractmethod
    def is_alive(self, record):
        """True if the environment still exists and is usable."""

    @abc.abstractmethod
    def destroy(self, record):
        """Remove the environment and everything it owns."""

    @abc.abstractmethod
    def has_tool(self, record, tool):
        """True if ``tool`` is available on PATH inside the environment."""

    # ------------------------------------------------------- template method

    def up(self, spec):
        log.info("[%s] provisioning %s (%s, python %s, %d packages)", spec.component,
                 spec.env_id, self.name, spec.python, len(spec.requirements))
        self.preflight(spec)
        record = self.create(spec)
        try:
            self.verify_system_tools(record, spec)
            self.install(record, spec)
            self.run_setup(record, spec)
        except Exception:
            record.status = "failed"
            raise
        record.status = "ready"
        return record

    def verify_system_tools(self, record, spec):
        missing = [t for t in spec.system if not self.has_tool(record, t)]
        if missing:
            raise ProvisionError("[{}] missing system tools: {}".format(
                spec.component, ", ".join(missing)))

    def run_setup(self, record, spec):
        for cmd in spec.setup:
            log.info("[%s] setup: %s", spec.component, cmd)
            self.exec(record, spec, cmd)

    def run_tests(self, record, spec):
        log.info("[%s] test: %s", spec.component, spec.test)
        return self.exec(record, spec, spec.test, check=False)
