from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

from asok.core.storage import LocalStorage, S3Storage, get_storage


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


def test_static_helper_s3() -> None:
    import asok.core.storage
    from asok.request.template import TemplateMixin

    class MockRequest(TemplateMixin):
        def __init__(self, environ):
            self.environ = environ

    mock_app = MagicMock()
    mock_app.config = {"DEBUG": True}
    req = MockRequest({"asok.root": "/tmp", "asok.app": mock_app})

    # Scenario 1: S3 static serving disabled (default)
    with patch.dict(os.environ, {"ASOK_SERVE_STATIC_FROM_S3": "false"}):
        url = req.static("css/app.css")
        assert url.startswith("/css/app.css?v=")

    # Scenario 2: S3 static serving enabled, but backend is local
    with patch.dict(os.environ, {
        "ASOK_SERVE_STATIC_FROM_S3": "true",
        "ASOK_STORAGE_BACKEND": "local"
    }):
        # Reset storage instance
        asok.core.storage._storage_instance = None
        url = req.static("css/app.css")
        assert url.startswith("/css/app.css?v=")

    # Scenario 3: S3 static serving enabled and backend is S3
    mock_boto3 = MagicMock()
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client
    mock_client.meta.region_name = "us-west-2"

    with patch.dict(sys.modules, {"boto3": mock_boto3}):
        with patch.dict(
            os.environ,
            {
                "ASOK_SERVE_STATIC_FROM_S3": "true",
                "ASOK_STORAGE_BACKEND": "s3",
                "ASOK_S3_BUCKET": "static-bucket",
                "ASOK_S3_REGION": "us-west-2",
            },
        ):
            # Reset storage instance
            asok.core.storage._storage_instance = None

            url = req.static("css/app.css")
            assert url.startswith("https://static-bucket.s3.us-west-2.amazonaws.com/css/app.css?v=")

            # Reset singleton
            asok.core.storage._storage_instance = None

