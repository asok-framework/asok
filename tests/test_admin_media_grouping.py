"""Tests for the Media Manager folder grouping.

Files uploaded through a model file field land in its `upload_to` subfolder, so
the admin groups /admin/media by folder and labels each folder with the model /
field that writes there — otherwise a busy uploads dir is an unreadable pile.
"""

import os

import pytest

from asok import Asok, Field, Model
from asok.admin import Admin
from asok.orm import MODELS_REGISTRY
from asok.testing import TestClient


class Product(Model):
    __tablename__ = "products_media"
    name = Field.String()
    image = Field.File(upload_to="products")


@pytest.fixture
def admin():
    app = Asok()
    app.config.update({"SECRET_KEY": "test-secret"})
    MODELS_REGISTRY["Product"] = Product
    a = Admin(app)
    a.register(Product)
    return a


def test_upload_folder_labels_maps_folder_to_model_field(admin):
    labels = admin._upload_folder_labels()
    assert "products" in labels
    assert "image" in labels["products"]  # "<label> · image"


def _mk(folder, name, mtime):
    return {"folder": folder, "name": name, "mtime": mtime}


def test_group_media_files_orders_named_first_root_last(admin):
    labels = admin._upload_folder_labels()
    files = [
        _mk("products", "a.png", "2026-07-24 10:00"),
        _mk("products", "b.png", "2026-07-24 11:00"),
        _mk("", "root.pdf", "2026-07-24 09:00"),
        _mk("avatars", "me.jpg", "2026-07-24 12:00"),
    ]
    groups = admin._group_media_files(files, labels)

    folders = [g["folder"] for g in groups]
    assert folders == ["avatars", "products", ""]  # named alpha, root last
    products = next(g for g in groups if g["folder"] == "products")
    assert products["count"] == 2
    assert "image" in products["label"]
    root = groups[-1]
    assert root["folder"] == "" and root["label"] == "" and root["count"] == 1


def test_group_media_files_empty(admin):
    assert admin._group_media_files([], {}) == []


def test_group_preview_is_capped_and_flags_more(admin):
    files = [_mk("products", f"{i}.png", "2026-07-24 10:00") for i in range(15)]
    groups = admin._group_media_files(files, {})
    g = groups[0]
    assert g["count"] == 15
    assert len(g["preview"]) == admin._MEDIA_PREVIEW_LIMIT == 12
    assert g["has_more"] is True


def test_group_preview_no_more_when_small(admin):
    files = [_mk("avatars", f"{i}.png", "2026-07-24 10:00") for i in range(3)]
    g = admin._group_media_files(files, {})[0]
    assert g["count"] == 3
    assert len(g["preview"]) == 3
    assert g["has_more"] is False


def test_paginate_media_math(admin):
    files = [_mk("products", f"{i}.png", "t") for i in range(100)]
    p1 = admin._paginate_media(files, 1)
    assert p1["total"] == 100 and p1["pages"] == 3 and len(p1["items"]) == 48
    p3 = admin._paginate_media(files, 3)
    assert p3["page"] == 3 and len(p3["items"]) == 4  # 100 - 96
    # out-of-range page clamps to the last page
    assert admin._paginate_media(files, 99)["page"] == 3


def test_media_page_renders_grouped_sections(tmp_path):
    """End-to-end: /admin/media renders a section per folder + an Uncategorized one."""
    app = Asok(root_dir=str(tmp_path))
    app.config["DATABASE_URL"] = f"sqlite:///{tmp_path / 'm.db'}"
    app.config["SECRET_KEY"] = "test-secret-key-12345"
    app.config["CSRF"] = False

    Product._app = app
    app.models = [Product]
    Product.create_table()

    # A file inside a model's upload_to folder, and one at the root.
    uploads = tmp_path / "src" / "partials" / "uploads"
    (uploads / "products").mkdir(parents=True, exist_ok=True)
    (uploads / "products" / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (uploads / "loose.txt").write_text("hi")

    admin = Admin(app, site_name="Test Admin")
    admin.register(Product)

    User = MODELS_REGISTRY["User"]
    User._app = app
    User.create_table()
    User.get_engine().execute("DELETE FROM users")
    u = User.create(_trust=True, email="a@t.com", password="secretpassword", is_admin=True)

    client = TestClient(app)
    sid = app._session_store.generate_sid()
    app._session_store.save(sid, {"user_id": u.id, "_user_id": u.id})
    client.cookies["asok_sid"] = app._sign(sid)

    res = client.get("/admin/media")
    assert res.status_code == 200
    assert "logo.png" in res.text
    assert "loose.txt" in res.text
    assert "image" in res.text  # "<label> · image" folder heading
    assert "Uncategorized" in res.text
    assert os.path.exists(uploads)  # sanity


def test_media_folder_view_is_paginated(tmp_path):
    """?folder=X drills into one folder with pagination (300-image folders)."""
    app = Asok(root_dir=str(tmp_path))
    app.config["DATABASE_URL"] = f"sqlite:///{tmp_path / 'm2.db'}"
    app.config["SECRET_KEY"] = "test-secret-key-12345"
    app.config["CSRF"] = False

    Product._app = app
    app.models = [Product]
    Product.create_table()

    uploads = tmp_path / "src" / "partials" / "uploads" / "products"
    uploads.mkdir(parents=True, exist_ok=True)
    for i in range(50):  # > _MEDIA_PER_PAGE (48) → 2 pages
        (uploads / f"p{i:03d}.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    admin = Admin(app, site_name="Test Admin")
    admin.register(Product)

    User = MODELS_REGISTRY["User"]
    User._app = app
    User.create_table()
    User.get_engine().execute("DELETE FROM users")
    u = User.create(_trust=True, email="a@t.com", password="secretpassword", is_admin=True)

    client = TestClient(app)
    sid = app._session_store.generate_sid()
    app._session_store.save(sid, {"user_id": u.id, "_user_id": u.id})
    client.cookies["asok_sid"] = app._sign(sid)

    # Overview: the folder is over the preview cap → a "View all" link appears.
    overview = client.get("/admin/media")
    assert overview.status_code == 200
    assert "folder=products" in overview.text
    assert "View all" in overview.text

    # Page 1 of the folder view: capped at 48, a Next link present.
    p1 = client.get("/admin/media?folder=products&page=1")
    assert p1.status_code == 200
    assert "1 / 2" in p1.text
    assert "All folders" in p1.text
    assert "page=2" in p1.text

    # Page 2: the remaining 2 files, a Previous link present.
    p2 = client.get("/admin/media?folder=products&page=2")
    assert p2.status_code == 200
    assert "2 / 2" in p2.text
    assert "page=1" in p2.text
