import pytest
from packaging.version import Version

from env_forge.errors import ResolutionError
from env_forge.resolver.requirements import (
    RequirementGraph, default_marker_env, find_drift)
from env_forge.resolver.resolver import Resolver


def resolve(index, lines, python="3.11", source="component:t"):
    env = default_marker_env(python)
    graph = RequirementGraph()
    for line in lines:
        graph.add_line(line, source, env)
    return Resolver(index, env).resolve(graph.constraint_map())


def pins(res):
    return {k: str(v) for k, v in res.pins.items()}


def test_picks_newest_compatible(static_index):
    res = resolve(static_index, ["requests"])
    assert pins(res) == {"requests": "2.32.3", "urllib3": "2.2.3", "idna": "3.10"}


def test_backtracks_when_dependency_conflicts(static_index):
    # legacy-sdk needs urllib3<2, so requests must fall back to a release allowing that
    res = resolve(static_index, ["requests", "legacy-sdk"])
    assert pins(res)["urllib3"] == "1.26.20"
    assert pins(res)["requests"] in {"2.32.3", "2.28.2"}


def test_conflict_names_sources(static_index):
    with pytest.raises(ResolutionError) as exc:
        resolve(static_index, ["legacy-sdk", "modern-sdk"])
    msg = str(exc.value)
    assert "urllib3" in msg
    assert "legacy-sdk==1.0.0" in msg or "modern-sdk==1.0.0" in msg


def test_extras_pull_in_optional_dependencies(static_index):
    res = resolve(static_index, ["requests[socks]"])
    assert "pysocks" in res.pins
    assert res.extras["requests"] == ["socks"]


def test_extras_not_installed_by_default(static_index):
    assert "pysocks" not in resolve(static_index, ["requests"]).pins


def test_markers_evaluated_for_target_python(static_index):
    assert "tomli" in resolve(static_index, ["pytest"], python="3.10").pins
    assert "tomli" not in resolve(static_index, ["pytest"], python="3.12").pins


def test_root_marker_skips_requirement(static_index):
    res = resolve(static_index, ["idna", "pyyaml; python_version < '3.0'"])
    assert "pyyaml" not in res.pins


def test_prereleases_skipped_unless_requested(static_index):
    assert pins(resolve(static_index, ["pyyaml"]))["pyyaml"] == "6.0.2"
    assert pins(resolve(static_index, ["pyyaml>=7.0.0rc1"]))["pyyaml"] == "7.0.0rc1"


def test_requires_python_filters_versions(static_index):
    with pytest.raises(ResolutionError, match="old-only"):
        resolve(static_index, ["old-only"], python="3.11")
    assert pins(resolve(static_index, ["old-only"], python="3.7"))["old-only"] == "0.9.0"


def test_unknown_package_fails(static_index):
    with pytest.raises(ResolutionError, match="does-not-exist"):
        resolve(static_index, ["does-not-exist"])


def test_required_by_tracks_roots_and_parents(static_index):
    res = resolve(static_index, ["requests"])
    assert res.required_by["requests"] == ["component:t"]
    assert res.required_by["urllib3"] == ["requests"]
    assert res.requires["requests"] == ["idna", "urllib3"]


def test_as_requirements_is_sorted_and_pinned(static_index):
    lines = resolve(static_index, ["requests[socks]"]).as_requirements()
    assert lines == sorted(lines)
    assert "requests[socks]==2.32.3" in lines


def test_find_drift():
    drift = find_drift({"a": {"x": "1"}, "b": {"x": "2"}, "c": {"y": "1"}})
    assert drift == {"x": {"1": ["a"], "2": ["b"]}}
