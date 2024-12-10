import os

import pytest

from env_forge.errors import ProvisionError
from env_forge.forge import EnvForge
from env_forge.provision import get_provisioner, to_argv
from env_forge.provision.docker import DockerProvisioner
from env_forge.provision.venv import VenvProvisioner
from env_forge.resolver import lockfile
from env_forge.resolver.index import build_index
from env_forge.runner import CommandResult, RecordingRunner


@pytest.fixture
def lock(manifest):
    return lockfile.build_lock(manifest, build_index(manifest.index, manifest.root))


@pytest.fixture
def forge(manifest, lock):
    return EnvForge(manifest, RecordingRunner(), lock=lock)


def test_to_argv_plain_and_shell():
    assert to_argv("pytest -q 'a b'") == ["pytest", "-q", "a b"]
    assert to_argv("make lint && pytest") == ["sh", "-c", "make lint && pytest"]
    assert to_argv(["x", 1]) == ["x", "1"]


def test_registry_lookup(tmp_path):
    r = RecordingRunner()
    assert isinstance(get_provisioner("venv", r, str(tmp_path)), VenvProvisioner)
    assert isinstance(get_provisioner("docker", r, str(tmp_path)), DockerProvisioner)
    with pytest.raises(ProvisionError, match="unknown provisioner"):
        get_provisioner("k8s", r, str(tmp_path))


def test_spec_contains_locked_requirements_and_paths(forge):
    spec = forge.spec_for("api")
    assert "requests==2.32.3" in spec.requirements
    assert spec.extra_paths == [os.path.join("libs", "common")]
    assert spec.env["APP_ENV"] == "test"
    assert spec.env_id.startswith("demo-api-")


def test_env_id_changes_with_lock(forge, lock):
    before = forge.spec_for("api").env_id
    lock["components"]["api"]["packages"]["requests"]["version"] = "2.28.2"
    assert forge.spec_for("api").env_id != before


def test_venv_up_installs_with_no_deps_and_runs_setup(forge):
    record = forge.up("api")
    cmds = forge.runner.commands()
    assert any(" -m venv " in c for c in cmds)
    install = [c for c in cmds if "--no-deps -r" in c]
    assert install and install[0].endswith("requirements.lock.txt")
    assert any(c.startswith("python -m compileall") for c in cmds)
    setup_call = [c for c in forge.runner.calls if c["cmd"][:2] == ["python", "-m"]][0]
    assert setup_call["env"]["VIRTUAL_ENV"] == record.location
    assert setup_call["env"]["PYTHONPATH"].endswith(os.path.join("libs", "common"))
    assert forge.state.get(record.env_id).status == "ready"


def test_up_reuses_live_environment(forge, monkeypatch):
    record = forge.up("api")
    monkeypatch.setattr(VenvProvisioner, "is_alive", lambda self, rec: True)
    n = len(forge.runner.calls)
    again = forge.up("api")
    assert again.env_id == record.env_id
    assert len(forge.runner.calls) == n


def test_missing_interpreter_is_reported(manifest, lock):
    runner = RecordingRunner(available={"git"})
    forge = EnvForge(manifest, runner, lock=lock)
    with pytest.raises(ProvisionError, match="no python3.11 interpreter"):
        forge.up("common")


def test_docker_up_mounts_repo_and_installs(forge):
    record = forge.up("worker")
    cmds = forge.runner.commands()
    run = [c for c in cmds if c.startswith("docker run")][0]
    assert "-v {}:/workspace".format(forge.manifest.root) in run
    assert "-w /workspace/services/worker" in run
    assert "python:3.11-slim sleep infinity" in run
    assert any("/workspace/.envforge/envs/" in c and "--no-deps -r" in c for c in cmds)
    assert record.location.startswith("envforge-demo-worker-")


def test_docker_preflight_requires_daemon(forge):
    forge.runner.responses["docker info"] = CommandResult(["docker"], 1, "", "no daemon")
    with pytest.raises(ProvisionError, match="daemon is not reachable"):
        forge.up("worker")


def test_test_reports_failures_and_writes_summary(forge):
    forge.runner.responses["pytest -q"] = CommandResult(["pytest"], 1)
    outcomes = forge.test(["api", "common"])
    by_name = {o.component: o.passed for o in outcomes}
    assert by_name == {"api": False, "common": True}
    summary = os.path.join(forge.manifest.root, ".envforge", "reports", "summary.json")
    assert os.path.isfile(summary)


def test_down_removes_records(forge):
    forge.up("common")
    removed = forge.down("common")
    assert len(removed) == 1
    assert forge.state.for_component("common") == []
