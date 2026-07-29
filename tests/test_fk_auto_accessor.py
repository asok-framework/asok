"""Field.ForeignKey("Model") alone must expose the parent accessor (post.author),
resolving the string model name lazily.

Regression: build_foreign_key_property called `related_model.find(...)` on the raw
string ("User") -> str.find() TypeError, so the auto accessor was unusable and a
redundant Relation.BelongsTo was required.
"""

import pytest

from asok import Asok, Field, Model, Relation
from asok.orm import MODELS_REGISTRY


class FkAuthor(Model):
    __tablename__ = "authors_fk"
    name = Field.String()
    posts = Relation.HasMany("FkArticle", foreign_key="author_id")


class FkArticle(Model):
    __tablename__ = "articles_fk"
    title = Field.String()
    author_id = Field.ForeignKey("FkAuthor")  # column + `article.author` accessor (auto)


@pytest.fixture(autouse=True)
def _db():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret", "DEBUG": True})
    MODELS_REGISTRY["FkAuthor"] = FkAuthor
    MODELS_REGISTRY["FkArticle"] = FkArticle
    eng = FkAuthor.get_engine()
    for t in ("authors_fk", "articles_fk"):
        eng.execute(f"DROP TABLE IF EXISTS {t}")
    FkAuthor.create_table()
    FkArticle.create_table()
    return app


def test_foreign_key_alone_exposes_parent_accessor():
    a = FkAuthor.create(_trust=True, name="Alice")
    FkArticle.create(_trust=True, title="A1", author_id=a.id)
    art = FkArticle.where("title", "A1").first()
    assert art.author is not None
    assert art.author.name == "Alice"


def test_reverse_has_many_accessor():
    a = FkAuthor.create(_trust=True, name="Bob")
    FkArticle.create(_trust=True, title="B1", author_id=a.id)
    FkArticle.create(_trust=True, title="B2", author_id=a.id)
    assert {p.title for p in FkAuthor.find(id=a.id).posts} == {"B1", "B2"}


def test_accessor_returns_none_for_missing_parent():
    art = FkArticle.create(_trust=True, title="ghost", author_id=99999)
    assert FkArticle.find(id=art.id).author is None
