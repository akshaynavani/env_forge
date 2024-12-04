"""Provisioner registry so new backends can be plugged in by name."""

from env_forge.errors import ProvisionError
from env_forge.provision.docker import DockerProvisioner
from env_forge.provision.venv import VenvProvisioner

_REGISTRY = {}


def register(cls):
    """Class decorator / function registering a Provisioner subclass under ``cls.name``."""
    _REGISTRY[cls.name] = cls
    return cls


register(VenvProvisioner)
register(DockerProvisioner)


def available():
    return sorted(_REGISTRY)


def get_provisioner(name, runner, envs_dir):
    try:
        cls = _REGISTRY[name]
    except KeyError:
        raise ProvisionError("unknown provisioner '{}' (available: {})".format(
            name, ", ".join(available())))
    return cls(runner, envs_dir)
