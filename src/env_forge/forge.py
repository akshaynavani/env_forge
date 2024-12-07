"""High-level orchestration: manifest + lockfile + provisioners + state.

``EnvForge`` is what the CLI talks to. It turns components into ``EnvSpec`` objects,
reuses existing environments when their fingerprint still matches, and records
everything in the state store.
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import List, Optional

from env_forge import paths
from env_forge.errors import EnvForgeError, ProvisionError
from env_forge.log import get_logger
from env_forge.provision import EnvSpec, get_provisioner
from env_forge.resolver import lockfile
from env_forge.state import StateStore

log = get_logger("forge")


@dataclass
class TestOutcome:
    component: str
    env_id: str
    returncode: int
    duration: float
    error: Optional[str] = None

    @property
    def passed(self):
        return self.returncode == 0 and self.error is None


class EnvForge:
    def __init__(self, manifest, runner, lock=None, require_fresh_lock=True):
        self.manifest = manifest
        self.runner = runner
        self.lock = lock if lock is not None else lockfile.load_lock(lockfile.lock_path(manifest))
        if require_fresh_lock:
            lockfile.ensure_fresh(manifest, self.lock)
        self.state = StateStore(paths.state_file(manifest))
        self.envs_dir = paths.envs_dir(manifest)

    # --------------------------------------------------------------- specs

    def spec_for(self, name):
        m = self.manifest
        comp = m.component(name)
        deps = [c for c in m.select([name]) if c.name != name]
        return EnvSpec(
            project=m.project,
            component=comp.name,
            python=m.python_for(comp),
            requirements=lockfile.pinned_requirements(self.lock, comp.name),
            workdir=os.path.normpath(os.path.join(m.root, comp.path)),
            root=m.root,
            env=m.env_for(comp),
            setup=list(comp.setup),
            test=comp.test,
            system=list(comp.system),
            image=comp.image,
            extra_paths=[os.path.normpath(d.path) for d in deps],
        )

    def provisioner_for(self, name):
        comp = self.manifest.component(name)
        return get_provisioner(self.manifest.provisioner_for(comp), self.runner, self.envs_dir)

    # ----------------------------------------------------------- lifecycle

    def current(self, name):
        """Return the live record for a component whose fingerprint matches, else None."""
        spec = self.spec_for(name)
        prov = self.provisioner_for(name)
        rec = self.state.get(spec.env_id)
        if rec and rec.status == "ready" and prov.is_alive(rec):
            return rec
        return None

    def up(self, name, recreate=False):
        spec = self.spec_for(name)
        prov = self.provisioner_for(name)
        existing = self.state.get(spec.env_id)
        if existing and not recreate and existing.status == "ready" and prov.is_alive(existing):
            log.info("[%s] reusing %s", name, existing.env_id)
            return existing

        # retire stale environments of this component (old lock / python / image)
        for old in self.state.for_component(name):
            if old.env_id != spec.env_id or recreate:
                log.info("[%s] removing stale env %s", name, old.env_id)
                get_provisioner(old.provisioner, self.runner, self.envs_dir).destroy(old)
                self.state.remove(old.env_id)

        try:
            record = prov.up(spec)
        except EnvForgeError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            raise ProvisionError("[{}] provisioning failed: {}".format(name, exc))
        if not self.runner.dry_run:
            self.state.put(record)
        log.info("[%s] ready: %s", name, record.location)
        return record

    def down(self, name=None, all_envs=False):
        targets = self.state.all() if all_envs else self.state.for_component(name)
        for rec in targets:
            get_provisioner(rec.provisioner, self.runner, self.envs_dir).destroy(rec)
            if not self.runner.dry_run:
                self.state.remove(rec.env_id)
            log.info("[%s] destroyed %s", rec.component, rec.env_id)
        return targets

    def run(self, name, command):
        record = self.up(name)
        spec = self.spec_for(name)
        return self.provisioner_for(name).exec(record, spec, command, check=False)

    def test(self, names=None, ephemeral=False, fail_fast=False):
        outcomes = []
        selected = [c.name for c in self.manifest.select(names)] if names else \
            sorted(self.manifest.components)
        for name in selected:
            start = time.time()
            env_id = self.spec_for(name).env_id
            try:
                record = self.up(name)
                result = self.provisioner_for(name).run_tests(record, self.spec_for(name))
                outcome = TestOutcome(name, env_id, result.returncode, time.time() - start)
            except EnvForgeError as exc:
                outcome = TestOutcome(name, env_id, 1, time.time() - start, error=str(exc))
            finally:
                if ephemeral:
                    self.down(name)
            log.info("[%s] %s in %.1fs", name, "PASS" if outcome.passed else "FAIL",
                     outcome.duration)
            outcomes.append(outcome)
            if fail_fast and not outcome.passed:
                break
        self._write_report(outcomes)
        return outcomes

    def _write_report(self, outcomes):
        if self.runner.dry_run:
            return
        report_dir = os.path.join(paths.project_dir(self.manifest), "reports")
        os.makedirs(report_dir, exist_ok=True)
        path = os.path.join(report_dir, "summary.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({
                "project": self.manifest.project,
                "passed": all(o.passed for o in outcomes),
                "results": [dict(asdict(o), passed=o.passed) for o in outcomes],
            }, fh, indent=2)
        log.debug("wrote %s", path)
