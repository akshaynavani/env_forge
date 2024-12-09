import os

import pytest

from env_forge.errors import ManifestError
from env_forge.manifest import find_manifest, parse_manifest, read_requirements_file


def test_loads_components(manifest):
    assert manifest.project == "demo"
    assert sorted(manifest.components) == ["api", "common", "worker"]
    assert manifest.provisioner_for(manifest.component("worker")) == "docker"
    assert manifest.provisioner_for(manifest.component("api")) == "venv"


def test_env_merges_defaults_and_component(manifest):
    env = manifest.env_for(manifest.component("api"))
    assert env == {"GLOBAL": "1", "APP_ENV": "test"}


def test_requirements_files_follow_includes(manifest):
    lines = manifest.component("api").all_requirement_lines(manifest.root)
    assert lines == ["requests>=2.30", "pytest"]


def test_select_includes_dependencies_first(manifest):
    assert [c.name for c in manifest.select(["api"])] == ["common", "api"]


def test_unknown_component(manifest):
    with pytest.raises(ManifestError, match="unknown component"):
        manifest.component("nope")


def test_fingerprint_changes_with_requirements(manifest):
    before = manifest.fingerprint()
    manifest.component("common").requirements.append("certifi")
    assert manifest.fingerprint() != before


@pytest.mark.parametrize("data,msg", [
    ({"components": {"a": {}}}, "'project' is required"),
    ({"project": "x"}, "at least one component"),
    ({"project": "x", "version": 9, "components": {"a": {}}}, "unsupported manifest version"),
    ({"project": "x", "components": {"A B": {}}}, "invalid component name"),
    ({"project": "x", "components": {"a": {"bogus": 1}}}, "unknown keys"),
    ({"project": "x", "components": {"a": {"provisioner": "k8s"}}}, "is not one of"),
    ({"project": "x", "components": {"a": {"depends_on": ["b"]}}}, "unknown component 'b'"),
])
def test_validation_errors(tmp_path, data, msg):
    with pytest.raises(ManifestError, match=msg):
        parse_manifest(data, str(tmp_path))


def test_dependency_cycle_detected(tmp_path):
    data = {"project": "x", "components": {
        "a": {"depends_on": ["b"]}, "b": {"depends_on": ["a"]}}}
    with pytest.raises(ManifestError, match="dependency cycle"):
        parse_manifest(data, str(tmp_path))


def test_find_manifest_walks_up(repo):
    nested = os.path.join(repo, "services", "api")
    assert find_manifest(nested) == os.path.join(repo, "envforge.yaml")


def test_circular_requirements_include(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("-r b.txt\n")
    b.write_text("-r a.txt\n")
    with pytest.raises(ManifestError, match="circular"):
        read_requirements_file(str(a))
