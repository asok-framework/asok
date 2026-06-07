import os
import stat
import sys
from unittest.mock import MagicMock

import pytest

# Mock boto3 module before importing anything that might use it
mock_boto3 = MagicMock()
mock_s3_client = MagicMock()
mock_boto3.client.return_value = mock_s3_client
sys.modules["boto3"] = mock_boto3

from asok.core.storage import S3Storage  # noqa: E402
from asok.request.upload import UploadedFile  # noqa: E402


@pytest.fixture
def mock_upload_dir(monkeypatch, tmp_path):
    """Mock the upload directory to use tmp_path and prevent path traversal blocks."""
    monkeypatch.setattr(os, "getcwd", lambda: str(tmp_path))

    # Create src/partials/uploads structure
    partials_dir = tmp_path / "src" / "partials" / "uploads"
    partials_dir.mkdir(parents=True)
    return str(partials_dir)


def test_local_upload_permissions(mock_upload_dir):
    file_content = b"test content"
    f_pub = UploadedFile("public.txt", file_content, "text/plain")
    f_priv = UploadedFile("private.txt", file_content, "text/plain")

    # Save files locally (destinations relative to src/partials/uploads)
    pub_path = f_pub.save(
        "public.txt", validate=False, secure_filename=False, private=False
    )
    priv_path = f_priv.save(
        "private.txt", validate=False, secure_filename=False, private=True
    )

    assert os.path.exists(pub_path)
    assert os.path.exists(priv_path)

    # Get permissions
    pub_mode = stat.S_IMODE(os.stat(pub_path).st_mode)
    priv_mode = stat.S_IMODE(os.stat(priv_path).st_mode)

    # Assert permissions match expectations
    assert pub_mode == 0o644
    assert priv_mode == 0o600


def test_s3_storage_private_acl(monkeypatch):
    monkeypatch.setenv("ASOK_S3_BUCKET", "test-bucket")
    monkeypatch.setenv("ASOK_S3_REGION", "us-east-1")

    s3_store = S3Storage()

    # Reset mock client history
    mock_s3_client.put_object.reset_mock()

    # 1. Test public save (should NOT pass ACL parameter to default/avoid modern AWS ACL blocks)
    s3_store.save("test_pub.txt", b"content", "uploads", private=False)
    assert mock_s3_client.put_object.call_count == 1
    _, kwargs_pub = mock_s3_client.put_object.call_args
    assert "ACL" not in kwargs_pub
    assert kwargs_pub["Bucket"] == "test-bucket"
    assert kwargs_pub["Key"] == "uploads/test_pub.txt"

    # 2. Test private save (should pass ACL='private')
    s3_store.save("test_priv.txt", b"content", "uploads", private=True)
    assert mock_s3_client.put_object.call_count == 2
    _, kwargs_priv = mock_s3_client.put_object.call_args
    assert kwargs_priv.get("ACL") == "private"
    assert kwargs_priv["Bucket"] == "test-bucket"
    assert kwargs_priv["Key"] == "uploads/test_priv.txt"
