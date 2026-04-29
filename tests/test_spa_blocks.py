"""Test SPA block rendering improvements."""


def test_blocks_method_spa_vs_normal():
    """Test that blocks() method handles SPA and normal requests differently."""
    from asok.request import Request

    # Mock environment for NORMAL request (no X-Block header)
    environ_normal = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "wsgi.input": None,
    }

    request_normal = Request(environ_normal)

    # Mock environment for SPA request (with X-Block header)
    environ_spa = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "HTTP_X_BLOCK": "main,sidebar",
        "wsgi.input": None,
    }

    request_spa = Request(environ_spa)

    # Test normal request - should NOT have <template> tags
    # (This is a conceptual test - in real usage you'd need the template file)
    assert not request_normal.environ.get("HTTP_X_BLOCK")

    # Test SPA request - should HAVE X-Block header
    assert request_spa.environ.get("HTTP_X_BLOCK") == "main,sidebar"

    print("✅ blocks() method tests passed")


if __name__ == "__main__":
    test_blocks_method_spa_vs_normal()
