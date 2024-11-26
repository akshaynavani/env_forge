"""Requirement parsing and cross-component constraint bookkeeping."""

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name

from env_forge.errors import ManifestError


def parse_requirement(line, source="<inline>"):
    try:
        req = Requirement(line)
    except InvalidRequirement as exc:
        raise ManifestError("{}: invalid requirement '{}': {}".format(source, line, exc))
    if req.url:
        raise ManifestError(
            "{}: direct URL requirements are not supported by the resolver: {}".format(source, line)
        )
    return req


@dataclass
class Constraint:
    """A version specifier together with who asked for it."""

    specifier: SpecifierSet
    source: str  # e.g. "component:api" or "requests==2.31.0"

    def __str__(self):
        return "{} (from {})".format(self.specifier or "*", self.source)


@dataclass
class ConstraintSet:
    """All constraints and requested extras for one canonical package name."""

    name: str
    constraints: List[Constraint] = field(default_factory=list)
    extras: Set[str] = field(default_factory=set)

    def add(self, specifier, source, extras=()):
        self.constraints.append(Constraint(SpecifierSet(str(specifier)), source))
        self.extras.update(e.lower() for e in extras)

    @property
    def combined(self):
        spec = SpecifierSet()
        for c in self.constraints:
            spec &= c.specifier
        return spec

    def allows(self, version):
        return all(c.specifier.contains(version, prereleases=True) for c in self.constraints)

    def allows_prereleases(self):
        """Pre-releases are only considered if some constraint explicitly mentions one."""
        return any(s.prereleases for c in self.constraints for s in c.specifier)

    def copy(self):
        return ConstraintSet(self.name, list(self.constraints), set(self.extras))

    def sources(self):
        return sorted({c.source for c in self.constraints})


class RequirementGraph:
    """Root requirements gathered from one or more components."""

    def __init__(self):
        self.roots = OrderedDict()  # type: Dict[str, ConstraintSet]

    def add_line(self, line, source, marker_env=None):
        req = parse_requirement(line, source)
        if req.marker is not None and marker_env is not None:
            if not req.marker.evaluate(dict(marker_env, extra="")):
                return None
        name = canonicalize_name(req.name)
        cs = self.roots.setdefault(name, ConstraintSet(name))
        cs.add(req.specifier, source, req.extras)
        return req

    def add_component(self, component, root, marker_env=None):
        source = "component:{}".format(component.name)
        for line in component.all_requirement_lines(root):
            self.add_line(line, source, marker_env)

    def constraint_map(self):
        return OrderedDict((k, v.copy()) for k, v in self.roots.items())


def find_drift(locks):
    """Given {component: {package: version}}, report packages pinned to different versions.

    Returns {package: {version: [components]}} for packages with >1 distinct version.
    Drift between teams is legal (each env is isolated) but worth surfacing.
    """
    seen = {}  # type: Dict[str, Dict[str, List[str]]]
    for comp, pkgs in locks.items():
        for pkg, ver in pkgs.items():
            seen.setdefault(pkg, {}).setdefault(ver, []).append(comp)
    return {pkg: vers for pkg, vers in sorted(seen.items()) if len(vers) > 1}


def default_marker_env(python_version, platform="linux"):
    """Marker environment for the *target* environment, not the host running envforge."""
    py = str(python_version)
    parts = py.split(".")
    full = py if len(parts) >= 3 else "{}.{}.0".format(parts[0], parts[1] if len(parts) > 1 else "0")
    systems = {"linux": "Linux", "darwin": "Darwin", "win32": "Windows"}
    return {
        "python_version": ".".join(full.split(".")[:2]),
        "python_full_version": full,
        "implementation_name": "cpython",
        "platform_python_implementation": "CPython",
        "implementation_version": full,
        "sys_platform": platform,
        "platform_system": systems.get(platform, platform),
        "os_name": "nt" if platform == "win32" else "posix",
        "platform_machine": "x86_64",
        "platform_release": "",
        "platform_version": "",
    }
