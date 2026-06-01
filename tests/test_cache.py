"""
Tests for the cache module.
Uses the actual Cache API: Cache(backend, path) and set/get/delete methods.
"""

import time

import pytest

from asok.cache import Cache


class TestMemoryCache:
    @pytest.fixture
    def cache(self):
        return Cache(backend="memory")

    def test_set_and_get(self, cache):
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"

    def test_get_missing_returns_none(self, cache):
        assert cache.get("nonexistent_xyz") is None

    def test_delete(self, cache):
        cache.set("del_key", "val")
        cache.forget("del_key")
        assert cache.get("del_key") is None

    def test_overwrite(self, cache):
        cache.set("ow_key", "old")
        cache.set("ow_key", "new")
        assert cache.get("ow_key") == "new"

    def test_ttl_expiry(self, cache):
        cache.set("ttl_key", "value", ttl=1)
        assert cache.get("ttl_key") == "value"
        time.sleep(1.1)
        assert cache.get("ttl_key") is None

    def test_stores_complex_types(self, cache):
        data = {"users": [1, 2, 3], "total": 3}
        cache.set("complex", data)
        assert cache.get("complex") == data

    def test_stores_list(self, cache):
        cache.set("list_key", [1, 2, 3])
        assert cache.get("list_key") == [1, 2, 3]


class TestFileCache:
    @pytest.fixture
    def cache(self, tmp_path):
        return Cache(backend="file", path=str(tmp_path))

    def test_set_and_get(self, cache):
        cache.set("fc_key1", "file_value")
        assert cache.get("fc_key1") == "file_value"

    def test_get_missing_returns_none(self, cache):
        assert cache.get("fc_nonexistent_xyz") is None

    def test_delete(self, cache):
        cache.set("fc_del", "val")
        cache.forget("fc_del")
        assert cache.get("fc_del") is None

    def test_persists_complex_types(self, cache):
        data = {"a": 1, "b": [1, 2, 3]}
        cache.set("fc_complex", data)
        assert cache.get("fc_complex") == data


class TestRedisCache:
    @pytest.fixture
    def mock_redis(self):
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        store = {}

        def mock_get(key):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            return store.get(key)

        def mock_set(key, val):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            store[key] = val

        def mock_setex(key, ttl, val):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            store[key] = val

        def mock_delete(*keys):
            for k in keys:
                if isinstance(k, bytes):
                    k = k.decode("utf-8")
                store.pop(k, None)

        def mock_keys(pattern):
            import fnmatch

            if isinstance(pattern, bytes):
                pattern = pattern.decode("utf-8")
            return [
                k.encode("utf-8") if isinstance(k, str) else k
                for k in store.keys()
                if fnmatch.fnmatch(k, pattern)
            ]

        mock_client.get.side_effect = mock_get
        mock_client.set.side_effect = mock_set
        mock_client.setex.side_effect = mock_setex
        mock_client.delete.side_effect = mock_delete
        mock_client.keys.side_effect = mock_keys
        return mock_client

    @pytest.fixture
    def cache(self, mock_redis):
        import sys
        from unittest.mock import MagicMock, patch

        mock_redis_module = MagicMock()
        mock_redis_module.Redis.from_url.return_value = mock_redis

        with patch.dict(sys.modules, {"redis": mock_redis_module}):
            c = Cache(backend="redis", namespace="test_ns", prefix="test_pfx")
            c._redis = mock_redis
            return c

    def test_set_and_get(self, cache, mock_redis):
        cache.set("key1", "value1")
        assert cache.get("key1") == "value1"
        mock_redis.set.assert_called_once()

    def test_set_with_ttl(self, cache, mock_redis):
        cache.set("key_ttl", "value", ttl=10)
        assert cache.get("key_ttl") == "value"
        mock_redis.setex.assert_called_with("test_ns:test_pfx:key_ttl", 10, '"value"')

    def test_delete(self, cache, mock_redis):
        cache.set("del_key", "val")
        cache.forget("del_key")
        assert cache.get("del_key") is None
        mock_redis.delete.assert_called_with("test_ns:test_pfx:del_key")

    def test_flush(self, cache, mock_redis):
        cache.set("key1", "val1")
        cache.set("key2", "val2")
        cache.flush()
        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestCachePageDecorator:
    def test_csrf_token_not_cached(self):
        from asok.cache import Cache, cache_page

        # Create a mock Request
        class MockRequest:
            def __init__(self, path, token):
                self.method = "GET"
                self.path = path
                self.query_string = ""
                self.csrf_token_value = token

        cache = Cache(backend="memory")

        @cache_page(ttl=60, cache_instance=cache)
        def view_fn(request):
            return f"<html><head><meta name='csrf-token' content='{request.csrf_token_value}'></head><body>{request.csrf_token_value}</body></html>"

        # Request 1 (first user/session)
        req1 = MockRequest("/test-cached-csrf", "token_first_user")
        res1 = view_fn(req1)
        assert res1 == "<html><head><meta name='csrf-token' content='token_first_user'></head><body>token_first_user</body></html>"

        # Verify that the value saved in the cache database has the placeholder instead of the token
        raw_cached = cache.get("page_/test-cached-csrf")
        # Since we modified the response before storing, calling cache.get directly will return the version with placeholder
        assert raw_cached == "<html><head><meta name='csrf-token' content='__ASOK_CSRF_TOKEN_PLACEHOLDER__'></head><body>__ASOK_CSRF_TOKEN_PLACEHOLDER__</body></html>"

        # Request 2 (second user/session, hits cache)
        req2 = MockRequest("/test-cached-csrf", "token_second_user")
        res2 = view_fn(req2)
        assert res2 == "<html><head><meta name='csrf-token' content='token_second_user'></head><body>token_second_user</body></html>"

