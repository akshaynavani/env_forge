# env_forge — Implementation Plan

**Developer Environment & CI Tooling Automation**
Tech: Python, Bash, CI/CD, Linux, OOP

## Goal

A Python + Bash CLI (`envforge`) for large multi-team codebases that:

1. Reads a declarative manifest (`envforge.yaml`) describing the components of a repo
   (each with its own Python requirements, system tools, env vars and test command).
2. **Resolves dependencies** across all components: merges version constraints, detects
   cross-team conflicts, picks concrete versions (backtracking resolver against PyPI or an
   offline index) and writes a deterministic lockfile.
3. **Provisions isolated test environments** with a single command (`envforge up <component>`)
   using pluggable provisioners (local `venv` or Docker container), tracked in a state registry.
4. **Integrates with CI/CD**: generates GitHub Actions / GitLab CI pipelines from the manifest,
   and ships Bash entry points (`bootstrap.sh`, `ci-run.sh`) so CI and onboarding run the same path.
5. Includes `envforge doctor` to check host prerequisites (onboarding).

## Key design decisions

- **OOP + plugins**: `Provisioner`, `PackageIndex`, `CIGenerator` are abstract base classes with
  registries so teams can add backends without touching the core.
- **Standard library first**: only runtime deps are `PyYAML` and `packaging`. PyPI is queried via
  `urllib` against the JSON API; an offline `StaticIndex` (YAML/JSON) is used in tests and air-gapped CI.
- **Determinism**: lockfile is sorted, includes a manifest hash so `envforge up` refuses stale locks.
- **Safety**: subprocess calls go through one `runner` module (dry-run support, no `shell=True`).
- Python 3.9+ compatible; `src/` layout; pytest for tests.

## Phases

### Phase 1 — Package scaffolding, manifest model, CLI skeleton
Deliverables: installable package, `envforge` entry point with subcommands stubbed, manifest
loading + validation into dataclasses, logging and command runner utility.
Files: `pyproject.toml`, `src/env_forge/__init__.py`, `src/env_forge/__main__.py`,
`src/env_forge/cli.py`, `src/env_forge/manifest.py`, `src/env_forge/errors.py`,
`src/env_forge/log.py`, `src/env_forge/runner.py`, `examples/envforge.yaml`.

### Phase 2 — Dependency resolution and lockfile
Deliverables: requirement parsing/merging across components, conflict reporting that names
the owning components, `PackageIndex` ABC with `PyPIIndex` and `StaticIndex`, backtracking
resolver handling transitive deps + environment markers, lockfile read/write, `envforge lock`.
Files: `src/env_forge/resolver/{__init__,requirements,index,resolver,lockfile}.py`, `cli.py`,
`examples/offline-index.yaml`.

### Phase 3 — Environment provisioning
Deliverables: `Provisioner` ABC, `VenvProvisioner`, `DockerProvisioner`, provisioner registry,
JSON state registry of environments, `envforge up / down / list / run / test`, setup hooks,
env-var injection.
Files: `src/env_forge/provision/{__init__,base,venv,docker,registry}.py`,
`src/env_forge/state.py`, `cli.py`.

### Phase 4 — CI/CD integration and onboarding tooling
Deliverables: `CIGenerator` ABC with GitHub Actions and GitLab CI generators (matrix per
component, pip cache, lock check), `envforge ci generate`, `envforge doctor`, Bash scripts
`scripts/bootstrap.sh` (one-command onboarding) and `scripts/ci-run.sh` (CI entry point).
Files: `src/env_forge/ci/{__init__,base,github,gitlab}.py`, `src/env_forge/doctor.py`,
`scripts/bootstrap.sh`, `scripts/ci-run.sh`, `cli.py`.

### Phase 5 — Tests, examples, docs
Deliverables: pytest suite (manifest, requirements merge, resolver w/ static index, lockfile,
provisioner with fake runner, CI generators, CLI), Makefile, `.gitignore`, README with
architecture diagram.
Files: `tests/*`, `Makefile`, `.gitignore`, `README.md`.
