from __future__ import annotations

import json
import os
import random
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Type

if TYPE_CHECKING:
    from .model import Model

_routing_state = threading.local()

DATABASES: Dict[str, str] = {}
REPLICAS: List[str] = []
SHARDS: Dict[str, Dict[str, Any]] = {}

_round_robin_counters: Dict[tuple, int] = {}
_round_robin_lock = threading.Lock()


class BaseDatabaseRouter:
    """Base class for custom database routers."""

    def db_for_read(self, model: Type[Model], **hints: Any) -> Optional[str]:
        """Return the database connection URL/DSN or alias for a read operation."""
        return None

    def db_for_write(self, model: Type[Model], **hints: Any) -> Optional[str]:
        """Return the database connection URL/DSN or alias for a write operation."""
        return None


ROUTERS: List[BaseDatabaseRouter] = []


def register_router(router: BaseDatabaseRouter) -> None:
    """Register a database router."""
    if router not in ROUTERS:
        ROUTERS.append(router)


def unregister_router(router: BaseDatabaseRouter) -> None:
    """Unregister a database router."""
    if router in ROUTERS:
        ROUTERS.remove(router)


def get_load_balancing_strategy() -> str:
    """Get the current replica load balancing strategy (round-robin or random)."""
    return (
        (os.getenv("DATABASE_LOAD_BALANCING_STRATEGY") or "round-robin").strip().lower()
    )


def select_replica(replicas_list: List[str], strategy: str = "round-robin") -> str:
    """Select a replica from a list of replica URLs based on the strategy."""
    if not replicas_list:
        raise ValueError("Replicas list is empty")
    if strategy == "random":
        return random.choice(replicas_list)

    key = tuple(replicas_list)
    with _round_robin_lock:
        if key not in _round_robin_counters:
            _round_robin_counters[key] = 0
        idx = _round_robin_counters[key]
        _round_robin_counters[key] = (idx + 1) % len(replicas_list)
    return replicas_list[idx]


# ── init_databases helpers ──────────────────────────────────────────────────

def _init_primary_db() -> None:
    """Populate DATABASES['default'] from DATABASE_URL or fallback to SQLite."""
    default_url = (os.getenv("DATABASE_URL") or "").strip()
    if not default_url:
        default_url = "db.sqlite3"
    DATABASES["default"] = default_url


def _parse_replicas_from_env(replicas_env: str) -> None:
    sep = ";" if ";" in replicas_env else ","
    REPLICAS.extend(r.strip() for r in replicas_env.split(sep) if r.strip())

def _parse_replicas_indexed() -> None:
    idx = 1
    while True:
        rep = os.getenv(f"DATABASE_REPLICA_{idx}")
        if not rep:
            break
        REPLICAS.append(rep.strip())
        idx += 1

def _init_replicas() -> None:
    """Populate REPLICAS from DATABASE_REPLICAS or indexed DATABASE_REPLICA_N vars."""
    replicas_env = os.getenv("DATABASE_REPLICAS")
    if replicas_env:
        _parse_replicas_from_env(replicas_env)
    else:
        _parse_replicas_indexed()


def _parse_shard_json(shards_json: str) -> None:
    """Parse DATABASE_SHARDS JSON and populate SHARDS."""
    try:
        parsed = json.loads(shards_json)
        if not isinstance(parsed, dict):
            return
        for k, v in parsed.items():
            name = k.lower()
            if isinstance(v, dict):
                SHARDS[name] = {"url": v.get("url", ""), "replicas": v.get("replicas", [])}
            else:
                SHARDS[name] = {"url": str(v), "replicas": []}
    except Exception:
        _parse_shard_kv(shards_json)


def _parse_shard_kv(shards_str: str) -> None:
    """Fallback: parse shards from key=value string (shard1=dsn1,shard2=dsn2)."""
    sep = ";" if ";" in shards_str else ","
    for pair in shards_str.split(sep):
        if "=" in pair:
            k, v = pair.split("=", 1)
            SHARDS[k.strip().lower()] = {"url": v.strip(), "replicas": []}


def _init_shards_from_json() -> None:
    """Parse DATABASE_SHARDS env variable into SHARDS."""
    shards_json = os.getenv("DATABASE_SHARDS")
    if shards_json:
        _parse_shard_json(shards_json)


def _process_shard_url_env(env_k: str, env_v: str) -> None:
    shard_name = env_k[len("DATABASE_SHARD_"):-len("_URL")].lower()
    SHARDS.setdefault(shard_name, {"url": "", "replicas": []})
    SHARDS[shard_name]["url"] = env_v.strip()

def _process_shard_replicas_env(env_k: str, env_v: str) -> None:
    shard_name = env_k[len("DATABASE_SHARD_"):-len("_REPLICAS")].lower()
    SHARDS.setdefault(shard_name, {"url": "", "replicas": []})
    sep = ";" if ";" in env_v else ","
    SHARDS[shard_name]["replicas"] = [r.strip() for r in env_v.split(sep) if r.strip()]

def _process_single_env_var(env_k: str, env_v: str) -> None:
    if env_k.startswith("DATABASE_SHARD_") and env_k.endswith("_URL"):
        _process_shard_url_env(env_k, env_v)
    elif env_k.startswith("DATABASE_SHARD_") and env_k.endswith("_REPLICAS"):
        _process_shard_replicas_env(env_k, env_v)

def _init_shards_from_env_vars() -> None:
    """Populate SHARDS from individual DATABASE_SHARD_<name>_URL/REPLICAS vars."""
    for env_k, env_v in os.environ.items():
        _process_single_env_var(env_k, env_v)


def _populate_databases_from_shards() -> None:
    """Copy shard URLs and replica DSNs into the DATABASES mapping."""
    for name, conf in SHARDS.items():
        DATABASES[f"shard_{name}"] = conf["url"]
        for i, rep_dsn in enumerate(conf["replicas"]):
            DATABASES[f"shard_{name}_replica_{i}"] = rep_dsn


def init_databases() -> None:
    """Initialize the database mappings from environment variables."""
    global DATABASES, REPLICAS, SHARDS
    DATABASES.clear()
    REPLICAS.clear()
    SHARDS.clear()

    _init_primary_db()
    _init_replicas()

    for i, rep_dsn in enumerate(REPLICAS):
        DATABASES[f"replica_{i}"] = rep_dsn

    _init_shards_from_json()
    _init_shards_from_env_vars()
    _populate_databases_from_shards()


# Run initialization
init_databases()


class DefaultRouter(BaseDatabaseRouter):
    """Default database router logic."""

    def _read_shard(self, shard: str, strategy: str) -> Optional[str]:
        """Return read DSN for a specific shard."""
        shard_conf = SHARDS.get(shard.lower())
        if not shard_conf:
            return None
        if shard_conf["replicas"]:
            return select_replica(shard_conf["replicas"], strategy)
        return shard_conf["url"]

    def db_for_read(self, model: Type[Model], **hints: Any) -> Optional[str]:
        shard = hints.get("shard")
        strategy = get_load_balancing_strategy()

        if shard:
            return self._read_shard(shard, strategy)

        # Check if the model has a custom db_path that is different from default
        primary_dsn = resolve_primary_dsn(model, shard=None)
        default_dsn = DATABASES.get("default") or "db.sqlite3"
        if primary_dsn != default_dsn:
            return None

        if REPLICAS:
            return select_replica(REPLICAS, strategy)
        return "default"

    def db_for_write(self, model: Type[Model], **hints: Any) -> Optional[str]:
        shard = hints.get("shard")
        if shard:
            shard_conf = SHARDS.get(shard.lower())
            if shard_conf:
                return shard_conf["url"]
            return None

        # Check if the model has a custom db_path that is different from default
        primary_dsn = resolve_primary_dsn(model, shard=None)
        default_dsn = DATABASES.get("default") or "db.sqlite3"
        if primary_dsn != default_dsn:
            return None

        return "default"


# Register default router
register_router(DefaultRouter())


@contextmanager
def database_router_context(op: str | None = None, shard: str | None = None):
    """Context manager to override query/routing operation and shard targets dynamically."""
    old_op = getattr(_routing_state, "op", None)
    old_shard = getattr(_routing_state, "shard", None)
    if op is not None:
        _routing_state.op = op
    if shard is not None:
        _routing_state.shard = shard
    try:
        yield
    finally:
        _routing_state.op = old_op
        _routing_state.shard = old_shard


def _resolve_model_dsn(model_cls: Type[Model]) -> str:
    raw_path = getattr(model_cls, "_db_path", None)
    resolved_path = os.getenv(raw_path) if raw_path else None
    if not resolved_path:
        resolved_path = raw_path
    if resolved_path:
        return resolved_path.strip()
    return DATABASES.get("default") or "db.sqlite3"


def resolve_primary_dsn(model_cls: Type[Model], shard: str | None = None) -> str:
    """Resolve the primary (write) DSN connection string for a given model and shard."""
    if shard:
        shard_conf = SHARDS.get(shard.lower())
        if shard_conf:
            return shard_conf["url"]
    return _resolve_model_dsn(model_cls)


def _route_via_registered_routers(model_cls: Type[Model], op: str, hints: dict) -> Optional[str]:
    for router in ROUTERS:
        if op == "read":
            target = router.db_for_read(model_cls, **hints)
        else:
            target = router.db_for_write(model_cls, **hints)
        if target is not None:
            if target in DATABASES:
                return DATABASES[target]
            return target
    return None


def _route_fallback_shard(op: str, shard: str) -> Optional[str]:
    shard_conf = SHARDS.get(shard.lower())
    if shard_conf:
        if op == "read" and shard_conf["replicas"]:
            return select_replica(
                shard_conf["replicas"], get_load_balancing_strategy()
            )
        return shard_conf["url"]
    return None


def _route_fallback_read_replica(model_cls: Type[Model]) -> Optional[str]:
    if REPLICAS:
        primary_dsn = resolve_primary_dsn(model_cls, shard=None)
        if primary_dsn == (DATABASES.get("default") or "db.sqlite3"):
            return select_replica(REPLICAS, get_load_balancing_strategy())
    return None


def _route_default_fallback(model_cls: Type[Model], op: str, shard: str | None) -> str:
    if shard:
        res = _route_fallback_shard(op, shard)
        if res is not None:
            return res
    elif op == "read":
        res = _route_fallback_read_replica(model_cls)
        if res is not None:
            return res
    return resolve_primary_dsn(model_cls, shard)


def route_database(model_cls: Type[Model], op: str, shard: str | None = None) -> str:
    """Find the connection DSN or path for the given operation using registered routers."""
    target = _route_via_registered_routers(model_cls, op, {"shard": shard})
    if target is not None:
        return target
    return _route_default_fallback(model_cls, op, shard)
