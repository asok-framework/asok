"""
Tests for the cache module.
Uses the actual Cache API: Cache(backend, path) and set/get/delete methods.
"""

import json
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

    def test_incr(self, cache):
        assert cache.incr("counter") == 1
        assert cache.incr("counter") == 2
        assert cache.incr("counter", amount=5) == 7
        assert cache.get("counter") == 7


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

    def test_incr(self, cache):
        assert cache.incr("fc_counter") == 1
        assert cache.incr("fc_counter") == 2
        assert cache.incr("fc_counter", amount=5) == 7
        assert cache.get("fc_counter") == 7


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

        def mock_scan_iter(match=None):
            import fnmatch

            if isinstance(match, bytes):
                match = match.decode("utf-8")
            for k in store.keys():
                if match is None or fnmatch.fnmatch(k, match):
                    yield k.encode("utf-8") if isinstance(k, str) else k

        def mock_incrby(key, amount=1):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            val_str = store.get(key)
            val = json.loads(val_str) if val_str else 0
            new_val = val + amount
            store[key] = json.dumps(new_val)
            return new_val

        def mock_expire(key, seconds):
            pass

        def mock_pipeline():
            mock_pipe = MagicMock()
            commands = []

            def pipe_incrby(key, amount=1):
                commands.append(("incrby", key, amount))
                return mock_pipe

            def pipe_ttl(key):
                commands.append(("ttl", key))
                return mock_pipe

            def pipe_execute():
                results = []
                for cmd in commands:
                    if cmd[0] == "incrby":
                        results.append(mock_incrby(cmd[1], cmd[2]))
                    elif cmd[0] == "ttl":
                        results.append(-1)
                return results

            mock_pipe.incrby.side_effect = pipe_incrby
            mock_pipe.ttl.side_effect = pipe_ttl
            mock_pipe.execute.side_effect = pipe_execute
            return mock_pipe

        mock_client.get.side_effect = mock_get
        mock_client.set.side_effect = mock_set
        mock_client.setex.side_effect = mock_setex
        mock_client.delete.side_effect = mock_delete
        mock_client.keys.side_effect = mock_keys
        mock_client.scan_iter.side_effect = mock_scan_iter
        mock_client.incrby.side_effect = mock_incrby
        mock_client.expire.side_effect = mock_expire
        mock_client.pipeline.side_effect = mock_pipeline
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

    def test_incr(self, cache, mock_redis):
        assert cache.incr("counter", ttl=60) == 1
        assert cache.incr("counter") == 2
        assert cache.get("counter") == 2
        mock_redis.incrby.assert_called_with("test_ns:test_pfx:counter", 1)
        mock_redis.expire.assert_called_with("test_ns:test_pfx:counter", 60)


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
        assert (
            res1
            == "<html><head><meta name='csrf-token' content='token_first_user'></head><body>token_first_user</body></html>"
        )

        # Verify that the value saved in the cache database has the placeholder instead of the token
        raw_cached = cache.get("page_/test-cached-csrf")
        # Since we modified the response before storing, calling cache.get directly will return the version with placeholder
        assert (
            raw_cached
            == "<html><head><meta name='csrf-token' content='__ASOK_CSRF_TOKEN_PLACEHOLDER__'></head><body>__ASOK_CSRF_TOKEN_PLACEHOLDER__</body></html>"
        )

        # Request 2 (second user/session, hits cache)
        req2 = MockRequest("/test-cached-csrf", "token_second_user")
        res2 = view_fn(req2)
        assert (
            res2
            == "<html><head><meta name='csrf-token' content='token_second_user'></head><body>token_second_user</body></html>"
        )
