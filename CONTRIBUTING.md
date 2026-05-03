# Contributing to Asok

First off, **thank you** for considering contributing to Asok! 🎉

Asok is a community-driven project, and we welcome contributions of all kinds - from bug reports to new features, documentation improvements to test coverage.

## 📋 Table of Contents

- [Code of Conduct](#code-of-conduct)
- [How Can I Contribute?](#how-can-i-contribute)
- [Development Setup](#development-setup)
- [Code Style Guidelines](#code-style-guidelines)
- [Commit Message Conventions](#commit-message-conventions)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Project Structure](#project-structure)

## 🤝 Code of Conduct

This project follows the [Contributor Covenant Code of Conduct](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you agree to uphold this code. Please report unacceptable behavior to [conduct@asok-framework.com](mailto:conduct@asok-framework.com).

**TL;DR**: Be respectful, be kind, be professional.

## 🌟 How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check existing issues to avoid duplicates. When creating a bug report, include:

- **Clear title** - Describe the issue concisely
- **Steps to reproduce** - Exact steps to trigger the bug
- **Expected behavior** - What should happen
- **Actual behavior** - What actually happens
- **Environment** - Python version, OS, Asok version
- **Code sample** - Minimal reproducible example

**Use the [Bug Report Template](https://github.com/asok-framework/asok/issues/new?template=bug_report.md)**

### Suggesting Features

Feature suggestions are welcome! Please:

- **Check existing discussions** - Your idea might already be proposed
- **Explain the use case** - Why is this feature needed?
- **Provide examples** - Show how it would work
- **Consider alternatives** - Are there other ways to achieve this?

**Start a [Feature Discussion](https://github.com/asok-framework/asok/discussions/new?category=ideas)**

### Improving Documentation

Documentation is crucial! You can help by:

- Fixing typos and grammar
- Adding examples
- Clarifying confusing sections
- Translating to other languages
- Adding missing API documentation

Documentation lives in the [asok-docs](https://github.com/asok-framework/asok-docs) repository.

### Writing Code

Great! Here's how to get started:

## 🛠️ Development Setup

### Prerequisites

- **Python 3.10+** (3.11 or 3.12 recommended)
- **Git**
- **Make** (optional, for shortcuts)

### Setup Steps

```bash
# 1. Fork the repository on GitHub
# Click the "Fork" button at https://github.com/asok-framework/asok

# 2. Clone your fork
git clone https://github.com/YOUR_USERNAME/asok.git
cd asok

# 3. Add upstream remote
git remote add upstream https://github.com/asok-framework/asok.git

# 4. Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 5. Install in development mode
pip install -e .

# 6. Install development dependencies (optional)
pip install pytest pytest-cov black isort mypy

# 7. Verify setup - run tests
python -m pytest

# You should see: 353 passed, 4 warnings
```

### Development Workflow

```bash
# 1. Create a feature branch
git checkout -b feature/your-feature-name

# 2. Make your changes
# Edit files in asok/ directory

# 3. Run tests frequently
python -m pytest

# 4. Run specific test file
python -m pytest tests/test_templates.py -v

# 5. Run tests with coverage
python -m pytest --cov=asok --cov-report=html

# 6. Format code (if you have black/isort)
black asok/
isort asok/

# 7. Commit your changes
git add .
git commit -m "feat: add amazing feature"

# 8. Push to your fork
git push origin feature/your-feature-name

# 9. Create Pull Request on GitHub
```

## 📐 Code Style Guidelines

### Python Style

Asok follows **PEP 8** with a few exceptions:

- **Line length**: 88 characters (Black default)
- **Quotes**: Double quotes for strings, single for chars
- **Type hints**: Required for public APIs
- **Docstrings**: Google style for public functions

```python
# Good ✅
def render_template(
    name: str,
    context: dict[str, Any],
    autoescape: bool = True,
) -> str:
    """Render a template with the given context.

    Args:
        name: Template filename relative to templates directory.
        context: Dictionary of variables to pass to template.
        autoescape: Whether to auto-escape HTML (default: True).

    Returns:
        Rendered template as string.

    Raises:
        TemplateNotFoundError: If template file doesn't exist.
    """
    ...

# Bad ❌
def render_template(name,context,autoescape=True):  # No types, no docs
    ...
```

### Code Organization

- **Keep files focused** - One responsibility per file
- **Avoid circular imports** - Use TYPE_CHECKING if needed
- **Minimize dependencies** - Stick to stdlib when possible
- **Document security** - Add `# SECURITY:` comments for security-critical code

```python
# SECURITY: Use constant-time comparison to prevent timing attacks
if not hmac.compare_digest(token, expected_token):
    raise InvalidTokenError()
```

## 📝 Commit Message Conventions

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <subject>

<body>

<footer>
```

### Types

- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation only
- `style:` - Code style (formatting, missing semicolons, etc.)
- `refactor:` - Code refactoring (no feature change)
- `perf:` - Performance improvement
- `test:` - Adding or updating tests
- `chore:` - Maintenance tasks

### Examples

```bash
# Good ✅
git commit -m "feat(templates): add filter blocks support"
git commit -m "fix(admin): resolve CSRF validation error"
git commit -m "docs: update contributing guide"

# Bad ❌
git commit -m "fixed stuff"
git commit -m "WIP"
git commit -m "asdfasdf"
```

### Detailed Commit

```bash
git commit -m "feat(orm): add support for UUID primary keys

Allows models to use UUID fields as primary keys instead of
auto-incrementing integers.

- Add Field.UUID() type
- Update migration engine to handle UUID columns
- Add tests for UUID primary keys

Closes #123"
```

## 🧪 Testing

### Running Tests

```bash
# All tests
python -m pytest

# Specific file
python -m pytest tests/test_orm.py

# Specific test
python -m pytest tests/test_orm.py::test_model_creation

# Verbose output
python -m pytest -v

# Show print statements
python -m pytest -s

# Coverage report
python -m pytest --cov=asok --cov-report=html
open htmlcov/index.html
```

### Writing Tests

- **Test files** go in `tests/` directory
- **Naming**: `test_<module>.py` (e.g., `test_templates.py`)
- **Test functions**: `test_<description>` (e.g., `test_csrf_token_rotation`)
- **Use descriptive names** - Test name should explain what it tests

```python
# Good ✅
def test_csrf_token_is_rotated_after_successful_validation():
    """CSRF tokens should rotate after validation to prevent reuse."""
    app = Asok()
    request = MockRequest(method="POST")

    original_token = request.csrf_token_value
    request.verify_csrf()

    assert request.csrf_token_value != original_token

# Bad ❌
def test_csrf():
    ...
```

### Test Coverage

- **Aim for 80%+** coverage on new code
- **Critical paths** (security, data integrity) should be 100%
- **Don't test for coverage** - Test for correctness

## 🔄 Pull Request Process

### Before Submitting

- [ ] Tests pass locally (`python -m pytest`)
- [ ] Code follows style guidelines
- [ ] Commit messages follow conventions
- [ ] Documentation updated (if needed)
- [ ] Added tests for new features
- [ ] No merge conflicts with `main`

### PR Guidelines

1. **Fill out the PR template** - Explain what and why
2. **Link related issues** - Use "Closes #123" or "Fixes #456"
3. **Keep PRs focused** - One feature/fix per PR
4. **Respond to reviews** - Address feedback promptly
5. **Be patient** - Reviews may take a few days

### PR Template

```markdown
## Description
Brief description of what this PR does

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation update

## Testing
How has this been tested?

## Checklist
- [ ] Tests pass locally
- [ ] Added tests for new features
- [ ] Updated documentation
- [ ] Follows code style guidelines
```

### Review Process

1. **Automated checks** run (tests, linting)
2. **Maintainer review** (usually within 1-3 days)
3. **Address feedback** if requested
4. **Approval** and merge by maintainer

## 📂 Project Structure

```
asok/
├── asok/                   # Main package
│   ├── __init__.py        # Package exports
│   ├── core.py            # Main Asok application class
│   ├── request.py         # Request/Response handling
│   ├── templates.py       # Template engine
│   ├── orm.py             # Database ORM
│   ├── forms.py           # Form handling & validation
│   ├── auth.py            # Authentication utilities
│   ├── validation.py      # Validation rules
│   ├── logger.py          # Logging utilities
│   ├── admin/             # Admin interface
│   │   ├── __init__.py
│   │   ├── templates/     # Admin HTML templates
│   │   └── static/        # Admin CSS/JS
│   └── ...
├── tests/                  # Test suite
│   ├── test_core.py
│   ├── test_templates.py
│   └── ...
├── examples/               # Example projects
├── docs/                   # Documentation (→ asok-docs repo)
├── README.md              # This file
├── CONTRIBUTING.md        # You are here!
├── LICENSE                # MIT License
└── pyproject.toml         # Package metadata
```

### Key Files to Know

- **`asok/core.py`** - Main application, routing, WSGI interface
- **`asok/request.py`** - Request/Response, sessions, CSRF
- **`asok/templates.py`** - Template compilation and rendering
- **`asok/orm.py`** - Database models and queries
- **`asok/admin/__init__.py`** - Admin interface logic

## 🎨 Areas That Need Help

Looking for where to start? Here are areas that always need attention:

### High Priority

- 🐛 **Bug fixes** - Check [open issues](https://github.com/asok-framework/asok/issues?q=is%3Aissue+is%3Aopen+label%3Abug)
- 📝 **Documentation** - Improve examples, fix typos
- 🧪 **Test coverage** - Add tests for edge cases
- 🌍 **Internationalization** - Translate docs and messages

### Medium Priority

- ⚡ **Performance** - Optimize hot paths
- 🎨 **Admin UI** - Improve design and UX
- 🔌 **Plugins** - Build ecosystem tools
- 📚 **Examples** - Create demo projects

### Good First Issues

Check issues labeled [`good first issue`](https://github.com/asok-framework/asok/labels/good%20first%20issue) - these are great for newcomers!

## 💬 Questions?

- **Discord** - Join our [Discord server](https://discord.com/invite/aYYkuPT3qR)
- **Discussions** - Ask in [GitHub Discussions](https://github.com/asok-framework/asok/discussions)
- **Email** - Reach out to [asok-framework@outlook.com](mailto:asok-framework@outlook.com)

## 🙏 Thank You!

Every contribution, no matter how small, makes Asok better. Thank you for being part of this journey!

---

**Happy coding!** 🚀
