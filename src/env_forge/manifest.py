"""Manifest model: parses and validates ``envforge.yaml``.

Example::

    version: 1
    project: payments-monorepo
    python: "3.11"
    index: pypi                     # or a path to an offline index file
    defaults:
      provisioner: venv
      env: {PYTHONDONTWRITEBYTECODE: "1"}
    components:
      api:
        path: services/api
        owner: team-payments
        requirements: ["fastapi>=0.100,<1", "pydantic>=2"]
        requirements_files: [requirements.txt]
        system: [git, make]
        env: {APP_ENV: test}
        setup: ["python -m compileall -q ."]
        test: "pytest -q"
"""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import yaml

from env_forge.errors import ManifestError

DEFAULT_MANIFEST = "envforge.yaml"
SUPPORTED_VERSIONS = (1,)
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
KNOWN_PROVISIONERS = ("venv", "docker")


@dataclass
class Component:
    name: str
    path: str = "."
    owner: Optional[str] = None
    requirements: List[str] = field(default_factory=list)
    requirements_files: List[str] = field(default_factory=list)
    system: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    setup: List[str] = field(default_factory=list)
    test: str = "pytest -q"
    provisioner: Optional[str] = None
    python: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    image: Optional[str] = None

    def all_requirement_lines(self, root):
        """Inline requirements plus the contents of any requirements files."""
        lines = list(self.requirements)
        for rel in self.requirements_files:
            path = os.path.join(root, self.path, rel)
            lines.extend(read_requirements_file(path))
        return lines


@dataclass
class Manifest:
    project: str
    root: str
    python: str = "3.11"
    index: str = "pypi"
    provisioner: str = "venv"
    env: Dict[str, str] = field(default_factory=dict)
    components: Dict[str, Component] = field(default_factory=dict)
    ci: Dict[str, object] = field(default_factory=dict)
    path: Optional[str] = None
    raw: Dict[str, object] = field(default_factory=dict, repr=False)

    def component(self, name):
        try:
            return self.components[name]
        except KeyError:
            raise ManifestError(
                "unknown component '{}' (known: {})".format(
                    name, ", ".join(sorted(self.components)) or "none"
                )
            )

    def select(self, names=None):
        """Return components by name (all when names is empty), plus their depends_on closure."""
        if not names:
            return [self.components[n] for n in sorted(self.components)]
        seen, ordered = set(), []

        def visit(n):
            if n in seen:
                return
            seen.add(n)
            comp = self.component(n)
            for dep in comp.depends_on:
                visit(dep)
            ordered.append(comp)

        for n in names:
            visit(n)
        return ordered

    def provisioner_for(self, comp):
        return comp.provisioner or self.provisioner

    def python_for(self, comp):
        return comp.python or self.python

    def env_for(self, comp):
        merged = dict(self.env)
        merged.update(comp.env)
        return merged

    def fingerprint(self):
        """Stable hash of everything that affects dependency resolution."""
        payload = {
            "python": self.python,
            "index": self.index,
            "components": {
                name: {
                    "python": c.python,
                    "requirements": sorted(c.all_requirement_lines(self.root)),
                }
                for name, c in sorted(self.components.items())
            },
        }
        blob = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()


def read_requirements_file(path, _seen=None):
    """Read a pip requirements file, following ``-r`` includes and dropping comments/options."""
    _seen = _seen or set()
    real = os.path.realpath(path)
    if real in _seen:
        raise ManifestError("circular requirements include: {}".format(path))
    _seen.add(real)
    if not os.path.isfile(path):
        raise ManifestError("requirements file not found: {}".format(path))
    lines = []
    with open(path, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ")):
                inc = line.split(None, 1)[1].strip()
                lines.extend(
                    read_requirements_file(os.path.join(os.path.dirname(path), inc), _seen)
                )
                continue
            if line.startswith("-"):
                # index-url, -e, --hash etc. are not handled by the resolver
                continue
            lines.append(line)
    return lines


def _require_type(value, typ, where):
    if not isinstance(value, typ):
        names = typ.__name__ if isinstance(typ, type) else "/".join(t.__name__ for t in typ)
        raise ManifestError("{} must be a {}, got {}".format(where, names, type(value).__name__))
    return value


def _str_map(value, where):
    _require_type(value, dict, where)
    return {str(k): str(v) for k, v in value.items()}


def _str_list(value, where):
    if isinstance(value, str):
        return [value]
    _require_type(value, list, where)
    return [str(v) for v in value]


def _parse_component(name, data):
    where = "components.{}".format(name)
    if not _NAME_RE.match(name):
        raise ManifestError("{}: invalid component name (use lowercase, digits, - _ .)".format(where))
    if data is None:
        data = {}
    _require_type(data, dict, where)
    known = set(Component.__dataclass_fields__) - {"name"}
    unknown = set(data) - known
    if unknown:
        raise ManifestError("{}: unknown keys: {}".format(where, ", ".join(sorted(unknown))))

    comp = Component(name=name)
    comp.path = str(data.get("path", "."))
    comp.owner = data.get("owner")
    comp.requirements = _str_list(data.get("requirements", []), where + ".requirements")
    comp.requirements_files = _str_list(
        data.get("requirements_files", []), where + ".requirements_files"
    )
    comp.system = _str_list(data.get("system", []), where + ".system")
    comp.env = _str_map(data.get("env", {}), where + ".env")
    comp.setup = _str_list(data.get("setup", []), where + ".setup")
    comp.test = str(data.get("test", comp.test))
    comp.provisioner = data.get("provisioner")
    comp.python = str(data["python"]) if "python" in data else None
    comp.depends_on = _str_list(data.get("depends_on", []), where + ".depends_on")
    comp.image = data.get("image")
    if comp.provisioner and comp.provisioner not in KNOWN_PROVISIONERS:
        raise ManifestError(
            "{}.provisioner: '{}' is not one of {}".format(where, comp.provisioner, KNOWN_PROVISIONERS)
        )
    return comp


def _check_dependency_graph(components):
    for comp in components.values():
        for dep in comp.depends_on:
            if dep not in components:
                raise ManifestError(
                    "components.{}.depends_on: unknown component '{}'".format(comp.name, dep)
                )
    state = {}

    def visit(name, stack):
        if state.get(name) == "done":
            return
        if state.get(name) == "active":
            cycle = stack[stack.index(name):] + [name]
            raise ManifestError("dependency cycle: {}".format(" -> ".join(cycle)))
        state[name] = "active"
        for dep in components[name].depends_on:
            visit(dep, stack + [name])
        state[name] = "done"

    for name in components:
        visit(name, [])


def parse_manifest(data, root, path=None):
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a mapping at the top level")
    version = data.get("version", 1)
    if version not in SUPPORTED_VERSIONS:
        raise ManifestError("unsupported manifest version {}".format(version))
    project = data.get("project")
    if not project:
        raise ManifestError("'project' is required")

    defaults = data.get("defaults", {}) or {}
    _require_type(defaults, dict, "defaults")
    provisioner = defaults.get("provisioner", "venv")
    if provisioner not in KNOWN_PROVISIONERS:
        raise ManifestError("defaults.provisioner: '{}' is not one of {}".format(
            provisioner, KNOWN_PROVISIONERS))

    comps_raw = data.get("components") or {}
    _require_type(comps_raw, dict, "components")
    if not comps_raw:
        raise ManifestError("at least one component is required")
    components = {name: _parse_component(str(name), body) for name, body in comps_raw.items()}
    _check_dependency_graph(components)

    ci = data.get("ci", {}) or {}
    _require_type(ci, dict, "ci")

    return Manifest(
        project=str(project),
        root=os.path.abspath(root),
        python=str(data.get("python", "3.11")),
        index=str(data.get("index", "pypi")),
        provisioner=provisioner,
        env=_str_map(defaults.get("env", {}), "defaults.env"),
        components=components,
        ci=ci,
        path=path,
        raw=data,
    )


def find_manifest(start="."):
    """Walk up from ``start`` looking for envforge.yaml (like git does for .git)."""
    cur = os.path.abspath(start)
    while True:
        candidate = os.path.join(cur, DEFAULT_MANIFEST)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def load_manifest(path=None):
    if path is None:
        path = find_manifest()
        if path is None:
            raise ManifestError(
                "no {} found in this directory or any parent".format(DEFAULT_MANIFEST)
            )
    if not os.path.isfile(path):
        raise ManifestError("manifest not found: {}".format(path))
    with open(path, "r", encoding="utf-8") as fh:
        try:
            data = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise ManifestError("invalid YAML in {}: {}".format(path, exc))
    return parse_manifest(data, root=os.path.dirname(os.path.abspath(path)), path=path)
