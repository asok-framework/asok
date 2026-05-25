.PHONY: install dev test coverage build clean lint format compile

PYTHON ?= python3

# Default command
all: lint test

# Compile core assets with esbuild
compile:
	$(PYTHON) scripts/compile_assets.py

# Install the framework in development mode
install:
	$(PYTHON) -m pip install -e .

# Run the development server (assumes you are in an Asok project directory)
dev:
	asok dev

# Run all tests
test:
	$(PYTHON) -m pytest tests/ -v

# Run tests with coverage report
coverage:
	$(PYTHON) -m pytest tests/ --cov=asok --cov-report=term-missing

# Lint the code using Ruff
lint:
	$(PYTHON) -m ruff check asok/ tests/

# Format the code using Ruff
format:
	$(PYTHON) -m ruff format asok/ tests/

# Build the distribution packages (.whl and .tar.gz)
build: clean compile
	$(PYTHON) -m build

# Clean generated files and caches
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
