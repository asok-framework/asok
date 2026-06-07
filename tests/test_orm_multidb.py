from __future__ import annotations

import json

import pytest

from asok.orm import Field, Model, Relation
from asok.orm.router import (
    DATABASES,
    init_databases,
)


class MultiDbUser(Model):
    name = Field.String()
    email = Field.String(unique=True)
    posts = Relation.HasMany("MultiDbPost", "user_id")


class MultiDbPost(Model):
    title = Field.String()
    user_id = Field.Integer()


@pytest.fixture(autouse=True)
def clean_multidb_config(tmp_path, monkeypatch):
    # Setup temp sqlite databases
    db_default = str(tmp_path / "default.db")
    db_replica1 = str(tmp_path / "replica1.db")
    db_replica2 = str(tmp_path / "replica2.db")
    db_shard1 = str(tmp_path / "shard1.db")
    db_shard1_rep = str(tmp_path / "shard1_replica.db")
    db_shard2 = str(tmp_path / "shard2.db")

    # Set env variables
    monkeypatch.setenv("DATABASE_URL", db_default)
    monkeypatch.setenv("DATABASE_REPLICAS", f"{db_replica1},{db_replica2}")

    # Configure shards
    shards_config = {
        "shard1": {
            "url": db_shard1,
            "replicas": [db_shard1_rep],
        },
        "shard2": db_shard2,
    }
    monkeypatch.setenv("DATABASE_SHARDS", json.dumps(shards_config))
    monkeypatch.setenv("DATABASE_LOAD_BALANCING_STRATEGY", "round-robin")

    # Re-initialize database mappings in router
    init_databases()

    # Create tables in all database engines
    MultiDbUser.close_connections()
    MultiDbPost.close_connections()

    # Temporarily bind models to each db URL to execute setup migrations
    for db_url in [
        db_default,
        db_replica1,
        db_replica2,
        db_shard1,
        db_shard1_rep,
        db_shard2,
    ]:
        monkeypatch.setattr(MultiDbUser, "_db_path", db_url)
        monkeypatch.setattr(MultiDbPost, "_db_path", db_url)
        MultiDbUser.get_engine().close_connections()
        MultiDbPost.get_engine().close_connections()
        MultiDbUser.create_table()
        MultiDbPost.create_table()

    # Reset monkeypatch values so dynamic routing is active
    monkeypatch.setattr(MultiDbUser, "_db_path", None)
    monkeypatch.setattr(MultiDbPost, "_db_path", None)

    yield {
        "default": db_default,
        "replica1": db_replica1,
        "replica2": db_replica2,
        "shard1": db_shard1,
        "shard1_replica": db_shard1_rep,
        "shard2": db_shard2,
    }

    # Cleanup connections
    from asok.orm import close_all_db_connections

    close_all_db_connections()


def test_databases_init():
    assert DATABASES["default"] is not None
    assert "replica_0" in DATABASES
    assert "replica_1" in DATABASES
    assert "shard_shard1" in DATABASES
    assert "shard_shard2" in DATABASES
    assert "shard_shard1_replica_0" in DATABASES


def test_read_replica_round_robin(clean_multidb_config):
    # Test round robin replica balancing
    # The default router balances between replica1 and replica2 for reads
    engine1 = MultiDbUser.get_engine(op="read")
    engine2 = MultiDbUser.get_engine(op="read")
    engine3 = MultiDbUser.get_engine(op="read")

    paths = [
        getattr(engine, "db_path", getattr(engine, "dsn", ""))
        for engine in [engine1, engine2, engine3]
    ]

    # Should alternate between replica1 and replica2
    assert clean_multidb_config["replica1"] in paths
    assert clean_multidb_config["replica2"] in paths


def test_transaction_pinning(clean_multidb_config):
    # Inside a transaction, all queries route to the default write instance
    with MultiDbUser.transaction():
        engine = MultiDbUser.get_engine(op="read")
        assert getattr(engine, "db_path", None) == clean_multidb_config["default"]


def test_sharding_routing(clean_multidb_config):
    # Check that writes to shard1 target primary shard db
    write_engine = MultiDbUser.get_engine(op="write", shard="shard1")
    assert getattr(write_engine, "db_path", None) == clean_multidb_config["shard1"]

    # Check that reads from shard1 target replica shard db
    read_engine = MultiDbUser.get_engine(op="read", shard="shard1")
    assert (
        getattr(read_engine, "db_path", None) == clean_multidb_config["shard1_replica"]
    )

    # Check that shard2 (no replica) routes both reads and writes to primary shard db
    s2_write = MultiDbUser.get_engine(op="write", shard="shard2")
    s2_read = MultiDbUser.get_engine(op="read", shard="shard2")
    assert getattr(s2_write, "db_path", None) == clean_multidb_config["shard2"]
    assert getattr(s2_read, "db_path", None) == clean_multidb_config["shard2"]


def test_sharding_crud_on_shard2(clean_multidb_config):
    # Create record on shard2
    user = MultiDbUser.on("shard2").create(name="Shard2User", email="s2@example.com")
    assert user.id is not None
    assert user._shard == "shard2"

    # Query shard2
    res = MultiDbUser.on("shard2").where("name", "Shard2User").get()
    assert len(res) == 1
    assert res[0].name == "Shard2User"
    assert res[0]._shard == "shard2"

    # Query default database (should be empty for Shard2User)
    res_default = MultiDbUser.where("name", "Shard2User").get()
    assert len(res_default) == 0


def test_sharding_relation_propagation(clean_multidb_config):
    # Setup user and post on shard2
    user = MultiDbUser.on("shard2").create(name="ShardOwner", email="owner@example.com")
    post1 = MultiDbPost.on("shard2").create(title="Shard 2 Post", user_id=user.id)
    assert post1._shard == "shard2"

    # Access posts relationship on user. Since user is on shard2, it should fetch posts from shard2.
    posts = user.posts
    assert len(posts) == 1
    assert posts[0].title == "Shard 2 Post"
    assert posts[0]._shard == "shard2"
