"""Registry of provisioned environments, persisted to ``.envforge/state.json``."""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from env_forge.errors import ProvisionError


@dataclass
class EnvRecord:
    env_id: str
    component: str
    provisioner: str
    location: str              # venv path or container name
    python: str
    fingerprint: str           # hash of python + pinned requirements
    created_at: float = field(default_factory=time.time)
    status: str = "ready"      # ready | failed | destroyed
    meta: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data):
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)


class StateStore:
    def __init__(self, path):
        self.path = path
        self._envs = {}  # type: Dict[str, EnvRecord]
        self._load()

    def _load(self):
        if not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except ValueError as exc:
            raise ProvisionError("corrupt state file {}: {}".format(self.path, exc))
        for env_id, rec in data.get("envs", {}).items():
            self._envs[env_id] = EnvRecord.from_dict(rec)

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"envs": {k: asdict(v) for k, v in sorted(self._envs.items())}},
                      fh, indent=2, sort_keys=True)
        os.replace(tmp, self.path)

    def put(self, record):
        self._envs[record.env_id] = record
        self.save()

    def remove(self, env_id):
        self._envs.pop(env_id, None)
        self.save()

    def get(self, env_id):
        return self._envs.get(env_id)

    def for_component(self, component):
        """Records for a component, newest first."""
        recs = [r for r in self._envs.values() if r.component == component]
        return sorted(recs, key=lambda r: r.created_at, reverse=True)

    def all(self):
        return sorted(self._envs.values(), key=lambda r: (r.component, -r.created_at))
