.PHONY: install dev test coverage build clean lint format compile complexity

PYTHON ?= python3

# Default command
all: lint complexity test

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

# Check cyclomatic complexity using Radon (Grade C or worse fails the check)
complexity:
	@echo "Checking cyclomatic complexity with Radon..."
	@OUTPUT=$$($(PYTHON) -m radon cc asok/ -n B -s); \
	if [ -n "$$OUTPUT" ]; then \
		echo "Complexity violation! The following functions/methods exceed Grade A:"; \
		echo "$$OUTPUT"; \
		exit 1; \
	fi
	@echo "Complexity checks passed successfully (all functions/methods are Grade A)!"

# Build the distribution packages (.whl and .tar.gz)
build: clean compile
	$(PYTHON) -m build

# Clean generated files and caches
clean:
	rm -rf dist/ build/ *.egg-info .pytest_cache .ruff_cache
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
