import os

import pytest

from env_forge.errors import LockfileError
from env_forge.resolver import lockfile
from env_forge.resolver.index import build_index


@pytest.fixture
def index(manifest):
    return build_index(manifest.index, manifest.root)


def test_build_lock_resolves_component_with_dependencies(manifest, index):
    lock = lockfile.build_lock(manifest, index)
    api = lock["components"]["api"]["packages"]
    # api depends_on common, so common's idna constraint is folded in
    assert {"requests", "pytest", "idna", "urllib3"} <= set(api)
    assert lock["manifest_fingerprint"] == manifest.fingerprint()
    assert set(lock["components"]) == {"api", "common", "worker"}


def test_roundtrip_and_freshness(manifest, index, tmp_path):
    lock = lockfile.build_lock(manifest, index)
    path = str(tmp_path / "envforge.lock")
    lockfile.write_lock(path, lock)
    loaded = lockfile.load_lock(path)
    assert loaded == lock
    lockfile.ensure_fresh(manifest, loaded)

    manifest.component("api").requirements.append("pyyaml")
    with pytest.raises(LockfileError, match="out of date"):
        lockfile.ensure_fresh(manifest, loaded)


def test_partial_relock_keeps_other_components(manifest, index):
    full = lockfile.build_lock(manifest, index)
    full["components"]["worker"]["packages"]["sentinel"] = {"version": "1", "required_by": []}
    partial = lockfile.build_lock(manifest, index, ["api"], previous=full)
    assert "sentinel" in partial["components"]["worker"]["packages"]


def test_pinned_requirements(manifest, index):
    lock = lockfile.build_lock(manifest, index)
    lines = lockfile.pinned_requirements(lock, "worker")
    assert lines == ["pyyaml==6.0.2"]
    with pytest.raises(LockfileError):
        lockfile.pinned_requirements(lock, "missing")


def test_missing_and_corrupt_lock(tmp_path):
    with pytest.raises(LockfileError, match="not found"):
        lockfile.load_lock(str(tmp_path / "nope.lock"))
    bad = tmp_path / "bad.lock"
    bad.write_text("{not json")
    with pytest.raises(LockfileError, match="corrupt"):
        lockfile.load_lock(str(bad))
