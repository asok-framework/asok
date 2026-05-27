from __future__ import annotations

import asyncio

import pytest

from asok.orm import Field, Model


class AsyncUser(Model):
    name = Field.String()
    email = Field.String(unique=True)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_async.db")
    AsyncUser.close_connections()
    monkeypatch.setattr(AsyncUser, "_db_path", db_path)
    AsyncUser.create_table()
    yield db_path
    AsyncUser.close_connections()


def test_async_orm_methods() -> None:
    async def run() -> None:
        # Test create_async
        u1 = await AsyncUser.create_async(name="Alice", email="alice@example.com")
        assert u1.id is not None
        assert u1.name == "Alice"

        u2 = await AsyncUser.create_async(name="Bob", email="bob@example.com")
        assert u2.id is not None

        # Test find_async
        fetched = await AsyncUser.find_async(id=u1.id)
        assert fetched is not None
        assert fetched.name == "Alice"

        # Test all_async
        users = await AsyncUser.all_async()
        assert len(users) == 2

        # Test all_async with filter
        filtered_users = await AsyncUser.all_async(name="Bob")
        assert len(filtered_users) == 1
        assert filtered_users[0].email == "bob@example.com"

        # Test save_async
        u1.name = "Alice Updated"
        await u1.save_async()

        fetched_updated = await AsyncUser.find_async(id=u1.id)
        assert fetched_updated.name == "Alice Updated"

        # Test delete_async
        await u1.delete_async()
        fetched_deleted = await AsyncUser.find_async(id=u1.id)
        assert fetched_deleted is None

        remaining_users = await AsyncUser.all_async()
        assert len(remaining_users) == 1

    asyncio.run(run())
