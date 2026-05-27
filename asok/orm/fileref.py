from __future__ import annotations

import os


class FileRef(str):
    """String subclass representing a file URL in the database.

    Automatically handles the '/uploads/' prefix mapping while preserving the raw filename.
    """

    @staticmethod
    def _validate_path_component(component: str, param_name: str) -> None:
        """Validate that a path component doesn't contain path traversal sequences.

        SECURITY: Prevents path traversal attacks that could access files outside
        the uploads directory.

        Args:
            component: The path component to validate (filename or upload_to)
            param_name: Name of the parameter for error messages

        Raises:
            ValueError: If the component contains path traversal sequences
        """
        if not component:
            return

        # SECURITY: Block path traversal sequences
        if ".." in component:
            raise ValueError(
                f"SECURITY: Path traversal detected in {param_name}: "
                f"'..' sequences are not allowed"
            )

        # SECURITY: Block absolute paths
        if component.startswith("/") or component.startswith("\\"):
            raise ValueError(
                f"SECURITY: Absolute paths not allowed in {param_name}: "
                f"must be relative to uploads directory"
            )

        # SECURITY: Block Windows drive letters (C:, D:, etc.)
        if len(component) >= 2 and component[1] == ":":
            raise ValueError(
                f"SECURITY: Drive letters not allowed in {param_name}: "
                f"must be relative to uploads directory"
            )

        # SECURITY: Block null bytes that could truncate paths
        if "\x00" in component:
            raise ValueError(
                f"SECURITY: Null bytes not allowed in {param_name}"
            )

        # SECURITY: Block other control characters that could cause issues
        # Control characters are in range 0x00-0x1F (except tab 0x09 which we allow)
        for char in component:
            code = ord(char)
            if 0x00 <= code <= 0x1F and code != 0x09:
                raise ValueError(
                    f"SECURITY: Control characters not allowed in {param_name}"
                )

    def __new__(cls, name: str, upload_to: str = "") -> FileRef:
        if not name:
            instance = super().__new__(cls, "")
        else:
            # SECURITY: Validate path components to prevent path traversal
            cls._validate_path_component(name, "name")
            cls._validate_path_component(upload_to, "upload_to")

            from ..core.storage import get_storage
            url = get_storage().url(name, upload_to)
            instance = super().__new__(cls, url)
        instance.name = name
        return instance

    def __str__(self) -> str:
        s = super().__str__()
        if not s:
            return s
        if os.environ.get("IMAGE_OPTIMIZATION") == "true":
            if any(s.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png")):
                return s + ".webp"
        return s
