.PHONY: install dev test coverage build clean lint format compile complexity bandit vulture pyright audit secrets

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

# Run security analysis using Bandit (fails on High severity issues)
bandit:
	$(PYTHON) -m bandit -r asok/ -lll

# Audit dependencies for known CVE vulnerabilities
audit:
	$(PYTHON) -m pip_audit 2>&1 | grep -v "Cache entry deserialization failed" || true

# Check for hardcoded secrets, passwords and tokens using Dodgy
secrets:
	$(PYTHON) -m dodgy -i venv .venv build dist

# Find dead / unused code using Vulture
vulture:
	$(PYTHON) -m vulture asok/ || true

# Type checking using Pyright
pyright:
	pyright asok/ || true

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
