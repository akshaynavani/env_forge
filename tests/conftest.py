import os
import textwrap

import pytest

from env_forge.manifest import load_manifest
from env_forge.resolver.index import StaticIndex
from env_forge.runner import RecordingRunner

INDEX = {
    "requests": {
        "2.32.3": {"requires": ["urllib3>=1.21.1,<3", "idna>=2.5,<4",
                               "PySocks>=1.5.6; extra == 'socks'"],
                   "requires_python": ">=3.8"},
        "2.28.2": ["urllib3>=1.21.1,<1.27", "idna>=2.5,<4"],
    },
    "urllib3": {"2.2.3": [], "1.26.20": []},
    "idna": {"3.10": []},
    "pysocks": {"1.7.1": []},
    "pyyaml": {"6.0.2": [], "7.0.0rc1": []},
    "legacy-sdk": {"1.0.0": ["urllib3<2"]},
    "modern-sdk": {"1.0.0": ["urllib3>=2"]},
    "old-only": {"0.9.0": {"requires": [], "requires_python": "<3.8"}},
    "pytest": {"8.3.3": ["tomli>=1; python_version < '3.11'", "pluggy>=1.5"]},
    "pluggy": {"1.5.0": []},
    "tomli": {"2.0.2": []},
}


@pytest.fixture
def static_index():
    return StaticIndex(INDEX)


@pytest.fixture
def runner():
    return RecordingRunner()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(textwrap.dedent(text).lstrip())


@pytest.fixture
def repo(tmp_path):
    """A small monorepo on disk with a manifest and requirements files."""
    root = str(tmp_path)
    write(os.path.join(root, "envforge.yaml"), """
        version: 1
        project: demo
        python: "3.11"
        index: index.yaml
        defaults:
          provisioner: venv
          env: {GLOBAL: "1"}
        components:
          common:
            path: libs/common
            requirements: ["idna>=3"]
          api:
            path: services/api
            depends_on: [common]
            requirements_files: [requirements.txt]
            env: {APP_ENV: test}
            setup: ["python -m compileall -q ."]
            test: "pytest -q --maxfail=1"
          worker:
            path: services/worker
            provisioner: docker
            requirements: ["pyyaml>=6"]
        ci:
          providers: [github, gitlab]
          python_versions: ["3.10", "3.11"]
        """)
    write(os.path.join(root, "services/api/requirements.txt"), """
        # comment
        requests>=2.30  # inline comment
        -r requirements-test.txt
        --index-url https://example.invalid/simple
        """)
    write(os.path.join(root, "services/api/requirements-test.txt"), "pytest\n")
    os.makedirs(os.path.join(root, "libs/common"), exist_ok=True)
    os.makedirs(os.path.join(root, "services/worker"), exist_ok=True)

    import yaml
    with open(os.path.join(root, "index.yaml"), "w") as fh:
        yaml.safe_dump({"packages": INDEX}, fh)
    return root


@pytest.fixture
def manifest(repo):
    return load_manifest(os.path.join(repo, "envforge.yaml"))
