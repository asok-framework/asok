from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

logger = logging.getLogger("asok.image")


def is_image(filepath: str) -> bool:
    """Check if a file is an optimizable image based on extension.

    Args:
        filepath: Path to the file to check.

    Returns:
        True if the file is a JPG, JPEG, or PNG.
    """
    ext = os.path.splitext(filepath)[1].lower()
    return ext in (".jpg", ".jpeg", ".png")


def _validate_image_binary(bin_path: str, root: str) -> bool:
    """Check that the cwebp binary is within the allowed directory. Returns True if safe."""
    try:
        abs_root = os.path.abspath(root)
        allowed_dir = os.path.abspath(os.path.join(abs_root, ".asok", "bin"))
        abs_bin_path = os.path.abspath(bin_path)
        if os.path.commonpath([abs_bin_path, allowed_dir]) != allowed_dir:
            raise ValueError(f"Path traversal detected in binary path: {bin_path}")
    except Exception as e:
        logger.warning(f"Security validation failed for binary path: {e}")
        return False
    if not os.path.exists(bin_path):
        return False
    return True


def _has_suspicious_chars(filepath: str) -> bool:
    return any(c in filepath for c in [";", "&", "|", "`", "$", "(", ")"])


def _validate_image_filepath(filepath: str) -> bool:
    """Validate the input filepath for safety before subprocess execution."""
    if not os.path.exists(filepath):
        logger.warning(f"Input file does not exist: {filepath}")
        return False
    if not os.path.isfile(filepath):
        logger.warning(f"Input path is not a file: {filepath}")
        return False
    abs_filepath = os.path.abspath(filepath)
    if ".." in abs_filepath or _has_suspicious_chars(filepath):
        logger.warning(f"Suspicious characters detected in filepath: {filepath}")
        return False
    return True


def _resolve_cwebp_binary(root: str) -> str:
    from asok.cli import _tailwind_platform_suffix

    suffix = _tailwind_platform_suffix()
    name = "cwebp.exe" if suffix.endswith(".exe") else "cwebp"
    return os.path.join(root, ".asok", "bin", name)


def _run_cwebp(bin_path: str, abs_filepath: str, output_path: str) -> bool:
    try:
        cmd = [bin_path, "-q", "80", abs_filepath, "-o", output_path]
        subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        return True
    except Exception as e:
        logger.warning(f"Failed to optimize {abs_filepath}: {e}")
        return False


def _cleanup_original_image(filepath: str, keep_original: bool) -> None:
    if not keep_original:
        try:
            os.remove(filepath)
        except OSError:
            pass


def _get_root(root: Optional[str]) -> str:
    if root is None:
        return os.getcwd()
    return root


def optimize_image(
    filepath: str, root: Optional[str] = None, keep_original: bool = True
) -> Optional[str]:
    """Generate a .webp version of the image using cwebp binary.

    Args:
        filepath:      Input image path.
        root:          Project root directory (optional).
        keep_original: If False, the original file is deleted after optimization.

    Returns:
        The path to the generated WebP file, or None if optimization failed.
    """
    if not is_image(filepath):
        return None

    cwd_root = _get_root(root)
    bin_path = _resolve_cwebp_binary(cwd_root)

    if not _validate_image_binary(bin_path, cwd_root):
        return None

    if not _validate_image_filepath(filepath):
        return None

    abs_filepath = os.path.abspath(filepath)
    output_path = filepath + ".webp"

    if _run_cwebp(bin_path, abs_filepath, output_path):
        _cleanup_original_image(filepath, keep_original)
        return output_path

    return None
