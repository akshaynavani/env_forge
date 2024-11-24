"""Exception hierarchy. Every user-facing failure derives from EnvForgeError so the
CLI can print a clean message and exit non-zero instead of dumping a traceback."""


class EnvForgeError(Exception):
    """Base class for all env_forge errors."""

    exit_code = 1


class ManifestError(EnvForgeError):
    """The envforge.yaml manifest is missing or invalid."""

    exit_code = 2


class ResolutionError(EnvForgeError):
    """Dependencies could not be resolved to a consistent set."""

    exit_code = 3


class LockfileError(EnvForgeError):
    """The lockfile is missing, corrupt or out of date with the manifest."""

    exit_code = 4


class ProvisionError(EnvForgeError):
    """An environment could not be created, used or destroyed."""

    exit_code = 5


class CommandError(EnvForgeError):
    """An external command exited with a non-zero status."""

    exit_code = 6

    def __init__(self, cmd, returncode, output=""):
        self.cmd = list(cmd)
        self.returncode = returncode
        self.output = output
        super().__init__(
            "command failed (exit {}): {}".format(returncode, " ".join(self.cmd))
        )
