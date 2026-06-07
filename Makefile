PROJECT = pyrunner-ci

.PHONY: install test lint format clean build publish

install:
	uv pip install -e ".[dev]"

test:
	pytest tests/

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

build:
	python -m build
	twine check dist/*

publish: build
	twine upload dist/*

clean:
	rm -rf dist/ build/ *.egg-info .workspace/
	find . -type d -name __pycache__ -exec rm -rf {} +
