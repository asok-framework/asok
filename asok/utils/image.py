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

    root = root or os.getcwd()
    # Find binary
    from asok.cli import _tailwind_platform_suffix

    suffix = _tailwind_platform_suffix()
    name = "cwebp.exe" if suffix.endswith(".exe") else "cwebp"
    bin_path = os.path.join(root, ".asok", "bin", name)

    # SECURITY: Ensure that the binary stays strictly within the authorized .asok/bin directory
    try:
        abs_root = os.path.abspath(root)
        allowed_dir = os.path.abspath(os.path.join(abs_root, ".asok", "bin"))
        abs_bin_path = os.path.abspath(bin_path)
        if os.path.commonpath([abs_bin_path, allowed_dir]) != allowed_dir:
            raise ValueError(f"Path traversal detected in binary path: {bin_path}")
    except Exception as e:
        logger.warning(f"Security validation failed for binary path: {e}")
        return None

    if not os.path.exists(bin_path):
        return None

    output_path = filepath + ".webp"

    # Run cwebp
    try:
        cmd = [bin_path, "-q", "80", filepath, "-o", output_path]
        subprocess.run(cmd, check=True, capture_output=True)

        if not keep_original:
            try:
                os.remove(filepath)
            except OSError:
                pass

        return output_path
    except Exception as e:
        logger.warning(f"Failed to optimize {filepath}: {e}")
        return None
