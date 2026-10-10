.PHONY: install test lint fmt typecheck build demo clean

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e ".[dev]"
	pre-commit install

test:
	$(PYTHON) -m pytest

lint:
	ruff check src tests tools
	ruff format --check src tests tools

fmt:
	ruff check --fix src tests tools
	ruff format src tests tools

typecheck:
	mypy src

build:
	$(PYTHON) -m build

demo:
	$(PYTHON) tools/demo.py

clean:
	rm -rf dist build src/*.egg-info .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov .patchnote-cache
