.PHONY: install test lint fmt typecheck build clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"
	pre-commit install

test:
	$(PYTHON) -m pytest

lint:
	ruff check src tests
	ruff format --check src tests

fmt:
	ruff check --fix src tests
	ruff format src tests

typecheck:
	mypy src

build:
	$(PYTHON) -m build

clean:
	rm -rf dist build src/*.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov .patchnote-cache
