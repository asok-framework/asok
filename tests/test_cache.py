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
