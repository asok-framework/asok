from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger("asok.storage")


class BaseStorage(ABC):
    """Abstract base class representing a storage backend."""

    @abstractmethod
    def save(self, filename: str, content: bytes, upload_to: str = "") -> str:
        """Save a file and return its URL/path."""
        pass

    @abstractmethod
    def url(self, filename: str, upload_to: str = "") -> str:
        """Return the URL/path of a file."""
        pass

    @abstractmethod
    def delete(self, filename: str, upload_to: str = "") -> None:
        """Delete a file from the storage."""
        pass


class LocalStorage(BaseStorage):
    """Local disk storage backend."""

    def __init__(self) -> None:
        self.base_dir = os.path.abspath(os.path.join(os.getcwd(), "src/partials/uploads"))

    def save(self, filename: str, content: bytes, upload_to: str = "") -> str:
        dest_dir = os.path.join(self.base_dir, upload_to) if upload_to else self.base_dir
        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, filename)

        # SECURITY: Prevent path traversal attacks
        resolved_dest = os.path.realpath(dest_path)
        resolved_base = os.path.realpath(self.base_dir)
        if os.path.commonpath([resolved_dest, resolved_base]) != resolved_base:
            raise ValueError(f"Path traversal blocked: {filename}")

        with open(resolved_dest, "wb") as f:
            f.write(content)
        os.chmod(resolved_dest, 0o644)
        return resolved_dest

    def url(self, filename: str, upload_to: str = "") -> str:
        if upload_to:
            return f"/uploads/{upload_to}/{filename}"
        return f"/uploads/{filename}"

    def delete(self, filename: str, upload_to: str = "") -> None:
        dest_dir = os.path.join(self.base_dir, upload_to) if upload_to else self.base_dir
        dest_path = os.path.join(dest_dir, filename)
        try:
            resolved_dest = os.path.realpath(dest_path)
            resolved_base = os.path.realpath(self.base_dir)
            if os.path.commonpath([resolved_dest, resolved_base]) == resolved_base:
                if os.path.exists(resolved_dest):
                    os.remove(resolved_dest)
        except Exception as e:
            logger.warning(f"Failed to delete local file {filename}: {e}")


class S3Storage(BaseStorage):
    """S3-compatible cloud storage backend."""

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError:
            raise ImportError(
                "The 'boto3' library is required to use the S3 storage backend. "
                "Install it using 'pip install asok[s3]'."
            )

        self.bucket = os.environ.get("ASOK_S3_BUCKET") or os.environ.get("S3_BUCKET")
        if not self.bucket:
            raise ValueError("ASOK_S3_BUCKET / S3_BUCKET environment variable is required for S3 storage.")

        region = os.environ.get("ASOK_S3_REGION") or os.environ.get("AWS_DEFAULT_REGION")
        endpoint = os.environ.get("ASOK_S3_ENDPOINT")

        self.client = boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        )
        self.custom_domain = os.environ.get("ASOK_S3_CUSTOM_DOMAIN")

    def save(self, filename: str, content: bytes, upload_to: str = "") -> str:
        key = f"{upload_to}/{filename}" if upload_to else filename

        import mimetypes
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type:
            content_type = "application/octet-stream"

        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
            )
        except Exception as e:
            raise RuntimeError(f"S3 upload failed: {e}")

        return self.url(filename, upload_to)

    def url(self, filename: str, upload_to: str = "") -> str:
        key = f"{upload_to}/{filename}" if upload_to else filename
        if self.custom_domain:
            return f"https://{self.custom_domain}/{key}"

        region = self.client.meta.region_name
        if region and region != "us-east-1":
            return f"https://{self.bucket}.s3.{region}.amazonaws.com/{key}"
        return f"https://{self.bucket}.s3.amazonaws.com/{key}"

    def delete(self, filename: str, upload_to: str = "") -> None:
        key = f"{upload_to}/{filename}" if upload_to else filename
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.warning(f"Failed to delete {key} from S3: {e}")


_storage_instance: BaseStorage | None = None


def get_storage() -> BaseStorage:
    """Get the active storage backend based on configuration."""
    global _storage_instance
    if _storage_instance is None:
        backend = os.environ.get("ASOK_STORAGE_BACKEND", "local").lower()
        if backend == "s3":
            _storage_instance = S3Storage()
        else:
            _storage_instance = LocalStorage()
    return _storage_instance
