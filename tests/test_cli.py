import json
import os

import pytest

from env_forge.cli import main


@pytest.fixture
def mf(repo):
    return os.path.join(repo, "envforge.yaml")


def test_validate_and_show(mf, capsys):
    assert main(["-f", mf, "validate"]) == 0
    assert "3 components" in capsys.readouterr().out
    assert main(["-f", mf, "show", "--json", "api"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert list(data["components"]) == ["common", "api"]


def test_lock_then_check_then_export(mf, repo, capsys, monkeypatch):
    monkeypatch.setenv("ENVFORGE_HOME", os.path.join(repo, ".cache"))
    assert main(["-f", mf, "lock"]) == 0
    assert os.path.isfile(os.path.join(repo, "envforge.lock"))
    assert main(["-f", mf, "lock", "--check"]) == 0
    capsys.readouterr()
    assert main(["-f", mf, "export", "worker"]) == 0
    assert capsys.readouterr().out == "pyyaml==6.0.2\n"


def test_lock_check_fails_without_lock(mf):
    assert main(["-f", mf, "lock", "--check"]) == 4


def test_invalid_manifest_exit_code(tmp_path):
    bad = tmp_path / "envforge.yaml"
    bad.write_text("project: x\ncomponents: {}\n")
    assert main(["-f", str(bad), "validate"]) == 2


def test_ci_generate_stdout(mf, capsys):
    assert main(["-f", mf, "ci", "generate", "--provider", "gitlab", "--stdout"]) == 0
    assert "lock-check" in capsys.readouterr().out


def test_ci_generate_writes_and_checks(mf, repo):
    assert main(["-f", mf, "ci", "generate"]) == 0
    assert os.path.isfile(os.path.join(repo, ".github", "workflows", "envforge.yml"))
    assert os.path.isfile(os.path.join(repo, ".gitlab-ci.yml"))
    assert main(["-f", mf, "ci", "generate", "--check"]) == 0


def test_affected_with_explicit_files(mf, capsys):
    assert main(["-f", mf, "affected", "--json", "libs/common/x.py"]) == 0
    assert json.loads(capsys.readouterr().out) == ["api", "common"]


def test_dry_run_up_does_not_touch_state(mf, repo, monkeypatch):
    monkeypatch.setenv("ENVFORGE_HOME", os.path.join(repo, ".cache"))
    assert main(["-f", mf, "lock"]) == 0
    assert main(["-f", mf, "--dry-run", "up", "common"]) == 0
    assert not os.path.isfile(os.path.join(repo, ".envforge", "state.json"))
