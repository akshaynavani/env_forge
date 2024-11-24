"""Single choke point for running external commands.

All subprocess usage in env_forge goes through a ``Runner`` so that:
  * commands are always argv lists (never ``shell=True``),
  * ``--dry-run`` can be honoured everywhere,
  * tests can swap in a ``RecordingRunner`` and assert on the commands issued.
"""

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence

from env_forge.errors import CommandError
from env_forge.log import get_logger

log = get_logger("runner")


@dataclass
class CommandResult:
    cmd: List[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self):
        return self.returncode == 0


class Runner:
    """Executes commands with subprocess."""

    def __init__(self, dry_run=False):
        self.dry_run = dry_run

    def which(self, program):
        return shutil.which(program)

    def run(
        self,
        cmd: Sequence[str],
        cwd: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        check: bool = True,
        capture: bool = False,
        timeout: Optional[float] = None,
    ) -> CommandResult:
        cmd = [str(c) for c in cmd]
        log.debug("$ %s%s", " ".join(cmd), " (cwd={})".format(cwd) if cwd else "")
        if self.dry_run:
            log.info("[dry-run] %s", " ".join(cmd))
            return CommandResult(cmd, 0)

        full_env = None
        if env is not None:
            full_env = dict(os.environ)
            full_env.update({k: str(v) for k, v in env.items()})

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=full_env,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                universal_newlines=True,
                timeout=timeout,
            )
        except FileNotFoundError:
            raise CommandError(cmd, 127, "executable not found: {}".format(cmd[0]))
        except subprocess.TimeoutExpired:
            raise CommandError(cmd, 124, "timed out after {}s".format(timeout))

        result = CommandResult(cmd, proc.returncode, proc.stdout or "", proc.stderr or "")
        if check and not result.ok:
            raise CommandError(cmd, result.returncode, result.stderr or result.stdout)
        return result


@dataclass
class RecordingRunner(Runner):
    """Test double: records commands and returns canned results without executing."""

    responses: Dict[str, CommandResult] = field(default_factory=dict)
    calls: List[dict] = field(default_factory=list)
    available: Optional[set] = None
    dry_run: bool = False

    def which(self, program):
        if self.available is None:
            return "/usr/bin/{}".format(program)
        return "/usr/bin/{}".format(program) if program in self.available else None

    def run(self, cmd, cwd=None, env=None, check=True, capture=False, timeout=None):
        cmd = [str(c) for c in cmd]
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": dict(env or {})})
        key = " ".join(cmd)
        for prefix, result in self.responses.items():
            if key.startswith(prefix):
                if check and not result.ok:
                    raise CommandError(cmd, result.returncode, result.stderr)
                return result
        return CommandResult(cmd, 0)

    def commands(self):
        return [" ".join(c["cmd"]) for c in self.calls]
