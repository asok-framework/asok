from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from asok.core.storage import S3Storage, get_storage, LocalStorage


def test_local_storage(tmp_path, monkeypatch) -> None:
    # Set CWD to tmp_path to isolate disk actions
    monkeypatch.chdir(tmp_path)

    storage = LocalStorage()
    filename = "test.txt"
    content = b"hello world"
    
    # Save file
    dest = storage.save(filename, content)
    assert os.path.exists(dest)
    with open(dest, "rb") as f:
        assert f.read() == content

    # Check url generation
    assert storage.url(filename) == "/uploads/test.txt"
    assert storage.url(filename, "sub") == "/uploads/sub/test.txt"

    # Delete file
    storage.delete(filename)
    assert not os.path.exists(dest)


def test_s3_storage_mocked() -> None:
    mock_boto3 = MagicMock()
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client
    mock_client.meta.region_name = "us-west-2"

    with patch.dict(sys.modules, {"boto3": mock_boto3}):
        with patch.dict(
            os.environ,
            {
                "ASOK_STORAGE_BACKEND": "s3",
                "ASOK_S3_BUCKET": "test-bucket",
                "ASOK_S3_REGION": "us-west-2",
            },
        ):
            # Reset storage instance for test
            import asok.core.storage

            asok.core.storage._storage_instance = None

            storage = get_storage()
            assert isinstance(storage, S3Storage)

            # Test save
            url = storage.save("logo.png", b"file content", "images")
            assert (
                url
                == "https://test-bucket.s3.us-west-2.amazonaws.com/images/logo.png"
            )
            mock_client.put_object.assert_called_with(
                Bucket="test-bucket",
                Key="images/logo.png",
                Body=b"file content",
                ContentType="image/png",
            )

            # Test delete
            storage.delete("logo.png", "images")
            mock_client.delete_object.assert_called_with(
                Bucket="test-bucket", Key="images/logo.png"
            )

            # Reset singleton
            asok.core.storage._storage_instance = None
