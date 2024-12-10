PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.PHONY: help venv install test cov lint example clean

help:
	@echo "make install  - create .venv and install env-forge in editable mode with dev extras"
	@echo "make test     - run the pytest suite"
	@echo "make cov      - run tests with coverage"
	@echo "make lint     - shellcheck the bash scripts (if installed)"
	@echo "make example  - lock and dry-run the example monorepo"
	@echo "make clean    - remove build artefacts and local state"

$(BIN)/python:
	$(PYTHON) -m venv $(VENV)

venv: $(BIN)/python

install: venv
	$(BIN)/python -m pip install --upgrade pip
	$(BIN)/python -m pip install -e '.[dev]'

test:
	$(BIN)/python -m pytest

cov:
	$(BIN)/python -m pytest --cov=env_forge --cov-report=term-missing

lint:
	@command -v shellcheck >/dev/null && shellcheck scripts/*.sh || echo "shellcheck not installed; skipping"

example:
	cd examples && ../$(BIN)/envforge lock && ../$(BIN)/envforge --dry-run up

clean:
	rm -rf build dist *.egg-info src/*.egg-info .pytest_cache .coverage htmlcov
	rm -rf examples/.envforge examples/envforge.lock
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
