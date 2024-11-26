"""Backtracking dependency resolver.

The resolver works over canonical package names. At each step it picks the
unresolved package with the fewest remaining candidates (most-constrained-first),
tries candidate versions newest-first, folds that version's dependencies into the
constraint set and recurses. If a choice leads to a dead end it backtracks.

When resolution fails, the resolver reports the package that ran out of candidates
and *who* constrained it, so a cross-team conflict reads like:

    no version of 'urllib3' satisfies all constraints:
      <2 (from component:legacy-billing)
      >=2.0 (from botocore==1.34.0)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from packaging.utils import canonicalize_name
from packaging.version import Version

from env_forge.errors import ResolutionError
from env_forge.log import get_logger
from env_forge.resolver.requirements import ConstraintSet

log = get_logger("resolver")


@dataclass
class Pin:
    version: Version
    extras: frozenset = frozenset()


@dataclass
class Resolution:
    pins: Dict[str, Version]
    extras: Dict[str, List[str]]
    requires: Dict[str, List[str]]          # package -> its resolved dependencies
    required_by: Dict[str, List[str]]       # package -> dependents ("component:x" for roots)

    def as_requirements(self):
        lines = []
        for name in sorted(self.pins):
            extras = self.extras.get(name) or []
            suffix = "[{}]".format(",".join(extras)) if extras else ""
            lines.append("{}{}=={}".format(name, suffix, self.pins[name]))
        return lines


@dataclass
class _Conflict:
    name: str
    constraints: List[str]
    depth: int


class Resolver:
    def __init__(self, index, marker_env, max_steps=50000, allow_prereleases=False):
        self.index = index
        self.marker_env = dict(marker_env)
        self.python_version = Version(self.marker_env.get("python_full_version", "3.11.0"))
        self.max_steps = max_steps
        self.allow_prereleases = allow_prereleases
        self._versions_cache = {}  # type: Dict[str, List[Version]]
        self._deps_cache = {}
        self._steps = 0
        self._conflict = None  # type: Optional[_Conflict]

    # ------------------------------------------------------------------ public

    def resolve(self, roots):
        """Resolve ``{name: ConstraintSet}`` root constraints into concrete pins."""
        self._steps = 0
        self._conflict = None
        constraints = {name: cs.copy() for name, cs in roots.items()}
        result = self._search({}, constraints, depth=0)
        if result is None:
            raise ResolutionError(self._explain())
        pins, final_constraints = result
        return self._build_resolution(pins, final_constraints)

    # ----------------------------------------------------------------- helpers

    def _all_versions(self, name):
        if name not in self._versions_cache:
            self._versions_cache[name] = sorted(self.index.versions(name), reverse=True)
        return self._versions_cache[name]

    def _python_ok(self, name, version):
        spec = self.index.requires_python(name, version)
        return spec is None or spec.contains(self.python_version, prereleases=True)

    def _candidates(self, name, cs, pins):
        pinned = pins.get(name)
        if pinned is not None:
            # already chosen; only revisited because new extras were requested
            return [pinned.version] if cs.allows(pinned.version) else []
        allow_pre = self.allow_prereleases or cs.allows_prereleases()
        out, pre = [], []
        for v in self._all_versions(name):
            if not cs.allows(v):
                continue
            (pre if v.is_prerelease else out).append(v)
        if allow_pre or not out:
            # like pip: fall back to pre-releases only when nothing else matches
            out = sorted(out + pre, reverse=True)
        return [v for v in out if self._python_ok(name, v)]

    def _marker_ok(self, req, extras):
        if req.marker is None:
            return True
        for extra in [""] + sorted(extras):
            env = dict(self.marker_env, extra=extra)
            if req.marker.evaluate(env):
                return True
        return False

    def _dependencies(self, name, version, extras):
        key = (name, str(version), tuple(sorted(extras)))
        if key not in self._deps_cache:
            reqs = self.index.dependencies(name, version)
            self._deps_cache[key] = [r for r in reqs if self._marker_ok(r, extras)]
        return self._deps_cache[key]

    def _record_conflict(self, name, cs, depth):
        if self._conflict is None or depth >= self._conflict.depth:
            self._conflict = _Conflict(name, [str(c) for c in cs.constraints], depth)

    def _explain(self):
        if self._conflict is None:
            return "dependency resolution failed"
        lines = ["no version of '{}' satisfies all constraints:".format(self._conflict.name)]
        lines.extend("  " + c for c in self._conflict.constraints)
        return "\n".join(lines)

    def _unresolved(self, pins, constraints):
        out = []
        for name, cs in constraints.items():
            pin = pins.get(name)
            if pin is None or not cs.extras <= pin.extras:
                out.append(name)
        return out

    # ------------------------------------------------------------------ search

    def _search(self, pins, constraints, depth):
        self._steps += 1
        if self._steps > self.max_steps:
            raise ResolutionError(
                "resolution exceeded {} steps; tighten constraints".format(self.max_steps)
            )

        unresolved = self._unresolved(pins, constraints)
        if not unresolved:
            return pins, constraints

        best_name, best_cands = None, None
        for name in unresolved:
            cands = self._candidates(name, constraints[name], pins)
            if not cands:
                self._record_conflict(name, constraints[name], depth)
                return None
            if best_cands is None or len(cands) < len(best_cands):
                best_name, best_cands = name, cands

        cs = constraints[best_name]
        for version in best_cands:
            source = "{}=={}".format(best_name, version)
            log.debug("%stry %s", "  " * min(depth, 20), source)
            new_constraints = {k: v.copy() for k, v in constraints.items()}
            ok = True
            for req in self._dependencies(best_name, version, cs.extras):
                dep = canonicalize_name(req.name)
                dep_cs = new_constraints.setdefault(dep, ConstraintSet(dep))
                if not any(c.source == source and c.specifier == req.specifier
                           for c in dep_cs.constraints):
                    dep_cs.add(req.specifier, source, req.extras)
                else:
                    dep_cs.extras.update(e.lower() for e in req.extras)
                pinned = pins.get(dep)
                if pinned is not None and not dep_cs.allows(pinned.version):
                    self._record_conflict(dep, dep_cs, depth + 1)
                    ok = False
                    break
            if not ok:
                continue
            new_pins = dict(pins)
            new_pins[best_name] = Pin(version, frozenset(cs.extras))
            found = self._search(new_pins, new_constraints, depth + 1)
            if found is not None:
                return found
        return None

    def _build_resolution(self, pins, constraints):
        requires = {}  # type: Dict[str, Set[str]]
        required_by = {name: set() for name in pins}
        for name, cs in constraints.items():
            for c in cs.constraints:
                parent = c.source.split("==", 1)[0] if "==" in c.source else c.source
                required_by.setdefault(name, set()).add(parent)
                if "==" in c.source:
                    requires.setdefault(parent, set()).add(name)
        return Resolution(
            pins={n: p.version for n, p in pins.items()},
            extras={n: sorted(p.extras) for n, p in pins.items() if p.extras},
            requires={n: sorted(v) for n, v in requires.items()},
            required_by={n: sorted(v) for n, v in required_by.items()},
        )
