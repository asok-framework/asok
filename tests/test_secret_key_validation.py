import os

import pytest

from asok import Asok


def test_secret_key_production_no_key(tmp_path):
    """Test that Asok raises RuntimeError in production mode if no SECRET_KEY is configured."""
    environ_override = {"DEBUG": "false"}
    # Remove SECRET_KEY if present
    if "SECRET_KEY" in os.environ:
        environ_override["SECRET_KEY"] = ""

    from unittest.mock import patch

    with patch.dict(os.environ, environ_override):
        if "SECRET_KEY" in os.environ:
            del os.environ["SECRET_KEY"]
        with pytest.raises(
            RuntimeError,
            match="SECRET_KEY environment variable is required in production",
        ):
            app = Asok()
            app.setup()


def test_secret_key_production_placeholder_key(tmp_path):
    """Test that Asok raises ValueError in production mode if the boilerplate placeholder SECRET_KEY is used."""
    environ_override = {
        "DEBUG": "false",
        "SECRET_KEY": "change-me-to-a-very-secure-production-secret-key-32-chars",
    }
    from unittest.mock import patch

    with patch.dict(os.environ, environ_override):
        with pytest.raises(
            ValueError,
            match="SECRET_KEY is set to the default boilerplate placeholder key",
        ):
            app = Asok()
            app.setup()


def test_secret_key_production_valid_key(tmp_path):
    """Test that Asok setup succeeds in production mode when a valid strong SECRET_KEY is provided."""
    environ_override = {"DEBUG": "false", "SECRET_KEY": "a" * 32}
    from unittest.mock import patch

    with patch.dict(os.environ, environ_override):
        app = Asok()
        # Should not raise any exception
        assert app.config["SECRET_KEY"] == "a" * 32
