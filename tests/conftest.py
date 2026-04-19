"""
Shared fixtures for the Asok test suite.
"""

import os
import sys
import tempfile

import pytest

# Make sure the local asok/ source is used (not a stale site-packages)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from asok.core import Asok
from asok.testing import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_app(**config) -> Asok:
    """Create a minimal Asok application for testing."""
    app = Asok()
    app.config["DEBUG"] = True
    app.config["SECRET_KEY"] = "test-secret-key-do-not-use-in-prod"
    app.config["DATABASE"] = ":memory:"
    for key, value in config.items():
        app.config[key] = value
    return app


# ---------------------------------------------------------------------------
# Session-scoped: one app + client for the whole test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def app():
    """A shared Asok app instance."""
    return make_app()


@pytest.fixture(scope="session")
def client(app):
    """A TestClient bound to the shared app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Function-scoped: fresh app for tests that mutate global state
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_app():
    """A brand-new Asok app for tests that need isolation."""
    return make_app()


@pytest.fixture
def fresh_client(fresh_app):
    """A TestClient bound to the fresh app."""
    return TestClient(fresh_app)


# ---------------------------------------------------------------------------
# Temporary directory for file-based tests
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """A temporary directory that is removed after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d
