.PHONY: install dev test coverage build clean lint format

# Default command
all: lint test

# Install the framework in development mode
install:
	python3 -m pip install -e .

# Run the development server (assumes you are in an Asok project directory)
dev:
	asok dev

# Run all tests
test:
	python3 -m pytest tests/ -v

# Run tests with coverage report
coverage:
	python3 -m pytest tests/ --cov=asok --cov-report=term-missing

# Lint the code using Ruff
lint:
	python3 -m ruff check asok/ tests/

# Format the code using Ruff
format:
	python3 -m ruff format asok/ tests/

# Build the distribution packages (.whl and .tar.gz)
build: clean
	python3 -m build

# Clean generated files and caches
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
