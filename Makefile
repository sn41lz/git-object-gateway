PYTHON ?= python3
VENV ?= .venv
COMPOSE ?= docker compose
RUNNER_UID ?= $(shell id -u)
RUNNER_GID ?= $(shell id -g)
RUNS ?= 5
COVERAGE_MIN ?= 90
VENV_PYTHON := $(VENV)/bin/python
RUN = RUNNER_UID=$(RUNNER_UID) RUNNER_GID=$(RUNNER_GID) $(COMPOSE) run --build --rm

.PHONY: help demo test benchmark serve setup local local-demo local-test local-benchmark lint format typecheck build

help:
	@echo 'Docker: make demo | test | benchmark [RUNS=5] | serve'
	@echo 'Local: make local (setup + demo + tests)'
	@echo 'Checks: make lint | typecheck | build'

demo:
	@mkdir -p artifacts
	$(RUN) runner python -m git_object_gateway demo --output artifacts/demo

test:
	@mkdir -p artifacts
	$(RUN) runner python -m pytest -p no:cacheprovider --cov --cov-fail-under=$(COVERAGE_MIN) --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml

benchmark:
	@mkdir -p artifacts
	$(RUN) runner python -m git_object_gateway benchmark --runs $(RUNS) --output artifacts/benchmark

serve:
	@test -n "$$GOG_REPO" || (echo 'Export GOG_REPO pointing to a complete local Git repository.' >&2; exit 1)
	@test -n "$$GOG_TOKEN" || (echo 'Export GOG_TOKEN with at least 32 ASCII characters.' >&2; exit 1)
	RUNNER_UID=$(RUNNER_UID) RUNNER_GID=$(RUNNER_GID) $(COMPOSE) --profile serve up --build gateway

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)

$(VENV)/.installed: requirements.lock requirements-dev.lock requirements-build.lock pyproject.toml | $(VENV_PYTHON)
	$(VENV_PYTHON) -m pip install --only-binary=:all: --require-hashes -r requirements-dev.lock
	$(VENV_PYTHON) -m pip install --no-deps --no-build-isolation --editable .
	@touch $@

setup: $(VENV)/.installed

local: setup
	$(MAKE) local-demo local-test

local-demo: setup
	$(VENV_PYTHON) -m git_object_gateway demo --output artifacts/demo

local-test: setup
	@mkdir -p artifacts
	$(VENV_PYTHON) -m pytest --cov --cov-fail-under=$(COVERAGE_MIN) --cov-report=term-missing --cov-report=xml:artifacts/coverage.xml

local-benchmark: setup
	$(VENV_PYTHON) -m git_object_gateway benchmark --runs $(RUNS) --output artifacts/benchmark

lint: setup
	$(VENV_PYTHON) -m ruff check .
	$(VENV_PYTHON) -m ruff format --check .

format: setup
	$(VENV_PYTHON) -m ruff check --fix .
	$(VENV_PYTHON) -m ruff format .

typecheck: setup
	$(VENV_PYTHON) -m mypy

build: setup
	$(VENV_PYTHON) -m build --no-isolation
