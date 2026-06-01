import os
import sys
import tempfile

from asok.cli.main import _add_virtualenv_to_path


def test_add_virtualenv_to_path_active_env():
    # Setup a temp directory to act as a mock virtualenv
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create lib/python3.14/site-packages
        py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        mock_site_packages = os.path.join(temp_dir, "lib", py_ver, "site-packages")
        os.makedirs(mock_site_packages, exist_ok=True)

        original_environ = dict(os.environ)
        original_path = list(sys.path)
        try:
            os.environ["VIRTUAL_ENV"] = temp_dir
            if mock_site_packages in sys.path:
                sys.path.remove(mock_site_packages)

            _add_virtualenv_to_path(None)

            assert mock_site_packages in sys.path
        finally:
            os.environ.clear()
            os.environ.update(original_environ)
            sys.path = original_path


def test_add_virtualenv_to_path_local_venv():
    # Setup a temp directory acting as the project root containing a .venv folder
    with tempfile.TemporaryDirectory() as project_root:
        venv_dir = os.path.join(project_root, ".venv")
        py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        mock_site_packages = os.path.join(venv_dir, "lib", py_ver, "site-packages")
        os.makedirs(mock_site_packages, exist_ok=True)

        original_environ = dict(os.environ)
        original_path = list(sys.path)
        try:
            # Ensure VIRTUAL_ENV is NOT set
            os.environ.pop("VIRTUAL_ENV", None)
            if mock_site_packages in sys.path:
                sys.path.remove(mock_site_packages)

            _add_virtualenv_to_path(project_root)

            assert mock_site_packages in sys.path
        finally:
            os.environ.clear()
            os.environ.update(original_environ)
            sys.path = original_path
