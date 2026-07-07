"""Property factories for ORM relations + i18n attribute pairs.

Metaclass property builders for relations and translatable attributes.
Pulled out of ModelMeta.__new__ so each builder stays at A complexity.
Returns the (getter, setter) descriptors the metaclass needs to attach.
"""

from __future__ import annotations

from typing import Any, Callable

from .list import ModelList
from .utils import MODELS_REGISTRY

# ── Foreign-key derived "parent" properties ─────────────────────────


def build_foreign_key_property(field_name: str, related_model: Any) -> property:
    def get_related(self):
        val = getattr(self, field_name)
        if not val:
            return None
        from .router import database_router_context

        with database_router_context(shard=getattr(self, "_shard", None)):
            return related_model.find(id=val)

    return property(get_related)


# ── Relation properties (HasMany / HasOne / BelongsTo / ...) ────────


def build_has_many_property(rel, rel_name: str) -> property:
    def get_collection(self):
        cached = _cached_relation(self, rel_name)
        if cached is not None:
            return cached
        target_model = MODELS_REGISTRY.get(rel.target_model_name)
        if not target_model:
            return []
        fk = rel.foreign_key or f"{self.__class__.__name__.lower()}_id"
        with _shard_context(self):
            return target_model.all(**{fk: self.id})

    return property(get_collection)


def build_has_one_property(rel, rel_name: str) -> property:
    def get_one(self):
        cached = _cached_relation(self, rel_name)
        if cached is not None:
            return cached
        target_model = MODELS_REGISTRY.get(rel.target_model_name)
        if not target_model:
            return None
        fk = rel.foreign_key or f"{self.__class__.__name__.lower()}_id"
        with _shard_context(self):
            return target_model.find(**{fk: self.id})

    return property(get_one)


def build_belongs_to_property(rel, rel_name: str) -> property:
    def get_parent(self):
        cached = _cached_relation(self, rel_name)
        if cached is not None:
            return cached
        target_model = MODELS_REGISTRY.get(rel.target_model_name)
        if not target_model:
            return None
        fk = rel.foreign_key or f"{rel.target_model_name.lower()}_id"
        val = getattr(self, fk, None)
        if not val:
            return None
        with _shard_context(self):
            return target_model.find(id=val)

    return property(get_parent)


def build_belongs_to_many_property(rel, rel_name: str) -> property:
    def get_many_to_many(self):
        cached = _cached_relation(self, rel_name)
        if cached is not None:
            return cached
        target_model = MODELS_REGISTRY.get(rel.target_model_name)
        if not target_model:
            return []
        return _execute_pivot_query(self, rel, target_model)

    return property(get_many_to_many)


def build_morph_to_property(rel, rel_name: str) -> property:
    def get_morph_to(self):
        cached = _cached_relation(self, rel_name)
        if cached is not None:
            return cached
        fk_id_name = rel.foreign_key or f"{rel_name}_id"
        fk_type_name = rel.owner_key or f"{rel_name}_type"
        target_id = getattr(self, fk_id_name, None)
        target_type = getattr(self, fk_type_name, None)
        if not target_id or not target_type:
            return None
        target_model = MODELS_REGISTRY.get(target_type)
        if not target_model:
            return None
        with _shard_context(self):
            return target_model.find(id=target_id)

    return property(get_morph_to)


def build_morph_many_property(rel, rel_name: str) -> property:
    def get_morph_many(self):
        cached = _cached_relation(self, rel_name)
        if cached is not None:
            return cached
        target_model = MODELS_REGISTRY.get(rel.target_model_name)
        if not target_model:
            return []
        fk_id = f"{rel.foreign_key}_id"
        fk_type = f"{rel.foreign_key}_type"
        with _shard_context(self):
            return (
                target_model.where(fk_id, self.id)
                .where(fk_type, self.__class__.__name__)
                .get()
            )

    return property(get_morph_many)


# ── Relation → builder dispatch ─────────────────────────────────────


_RELATION_BUILDERS: dict[str, Callable[..., property]] = {
    "HasMany": build_has_many_property,
    "HasOne": build_has_one_property,
    "BelongsTo": build_belongs_to_property,
    "BelongsToMany": build_belongs_to_many_property,
    "MorphTo": build_morph_to_property,
    "MorphMany": build_morph_many_property,
}


def build_relation_property(rel, rel_name: str) -> property | None:
    builder = _RELATION_BUILDERS.get(rel.type)
    if builder is None:
        return None
    return builder(rel, rel_name)


# ── Translatable field properties (e.g. title_fr, title_en) ─────────


def build_translatable_property(base_name: str) -> property:
    return property(
        _make_translatable_getter(base_name), _make_translatable_setter(base_name)
    )


def _make_translatable_getter(base_name: str):
    def getter(self):
        lang, default_lang = _current_locales()
        if val := _try_lang_field(self, base_name, lang):
            return val
        if val := _try_default_lang_or_base(self, base_name, lang, default_lang):
            return val
        if val := _try_fallback_lang(self, base_name, default_lang):
            return val
        if val := _try_any_translation_field(self, base_name):
            return val
        return self.__dict__.get(base_name)

    return getter


def _make_translatable_setter(base_name: str):
    def setter(self, value):
        lang, default_lang = _current_locales()
        target_field = f"{base_name}_{lang}"
        if lang == default_lang:
            self.__dict__[base_name] = value
            if target_field in self._fields:
                setattr(self, target_field, value)
            return
        if target_field in self._fields:
            setattr(self, target_field, value)
        else:
            self.__dict__[base_name] = value

    return setter


def _current_locales() -> tuple[str, str]:
    from ..context import current_request

    lang = _safe_request_lang(current_request)
    default_lang = _request_default_lang(current_request)
    return lang, default_lang


def _safe_request_lang(current_request) -> str:
    try:
        return current_request.lang if current_request else "en"
    except RuntimeError:
        return "en"


def _request_default_lang(current_request) -> str:
    app_ref = None
    if current_request and hasattr(current_request, "environ"):
        app_ref = current_request.environ.get("asok.app")
    return (app_ref.config.get("LOCALE") if app_ref else "en") or "en"


def _try_lang_field(self, base_name: str, lang: str):
    target = f"{base_name}_{lang}"
    if target in self._fields:
        return getattr(self, target, None)
    return None


def _try_default_lang_or_base(self, base_name: str, lang: str, default_lang: str):
    target_field = f"{base_name}_{lang}"
    if lang == default_lang or target_field not in self._fields:
        return self.__dict__.get(base_name)
    return None


def _try_fallback_lang(self, base_name: str, default_lang: str):
    default_field = f"{base_name}_{default_lang}"
    if default_field in self._fields:
        return getattr(self, default_field, None)
    if default_lang == "en":
        return self.__dict__.get(base_name)
    return None


def _try_any_translation_field(self, base_name: str):
    for f_name in self._fields:
        if f_name.startswith(f"{base_name}_"):
            val = getattr(self, f_name, None)
            if val:
                return val
    return None


# ── Internal helpers ───────────────────────────────────────────────


def _cached_relation(instance, rel_name: str):
    return instance.__dict__.get(f"_eager_{rel_name}")


def _shard_context(instance):
    from .router import database_router_context

    return database_router_context(shard=getattr(instance, "_shard", None))


def _execute_pivot_query(instance, rel, target_model):
    # SECURITY: _pivot_info validates identifiers before quoting them below.
    pivot, pfk, pofk = instance._pivot_info(rel)
    with _shard_context(instance):
        engine = instance.get_engine(op="read")
        q_target = engine.quote_identifier(target_model._table)
        q_pivot = engine.quote_identifier(pivot)
        q_pfk = engine.quote_identifier(pfk)
        q_pofk = engine.quote_identifier(pofk)
        sql = (
            f"SELECT t.* FROM {q_target} t "
            f"JOIN {q_pivot} p ON p.{q_pofk} = t.id "
            f"WHERE p.{q_pfk} = ?"
        )
        rows = engine.execute(sql, (instance.id,))
        results = ModelList(
            (target_model(**row) for row in rows),
            sql=sql,
            args=[instance.id],
        )
        for r in results:
            r._shard = getattr(instance, "_shard", None)
        return results
