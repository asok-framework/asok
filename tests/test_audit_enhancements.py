import pytest

from asok import Asok
from asok.forms import Form
from asok.orm import Field, Model
from asok.request import Request
from asok.templates.filters import _pluralize_filter


class Item(Model):
    name = Field.String()
    status = Field.String()


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    db_file = tmp_path / "test_enhancements.db"
    app = Asok()
    app.config["DATABASE_URL"] = f"sqlite:///{db_file}"
    Item._app = app
    Item.create_table()
    Item.query().delete()
    yield
    Item._app = None


def test_query_where_not_in():
    i1 = Item.create(name="Item 1", status="active")
    Item.create(name="Item 2", status="pending")
    i3 = Item.create(name="Item 3", status="deleted")

    # Exclude list of statuses
    active_items = Item.where_not_in("status", ["pending", "deleted"]).get()
    assert len(active_items) == 1
    assert active_items[0].id == i1.id

    # Exclude empty list (should return all)
    all_items = Item.where_not_in("status", []).get()
    assert len(all_items) == 3

    # Exclude using subquery
    pending_ids = Item.where("status", "pending").select("id")
    non_pending = Item.where_not_in("id", pending_ids).get()
    assert len(non_pending) == 2
    assert {i.id for i in non_pending} == {i1.id, i3.id}


def test_form_submission_put_and_patch():
    fields = {"title": Form.text("Title", "required")}

    # Test PUT form submission
    environ_put = {
        "REQUEST_METHOD": "PUT",
        "PATH_INFO": "/item/1",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": None,
    }
    req_put = Request(environ_put)
    req_put.form = {"title": "Updated Title"}

    form_put = Form(fields, req_put)
    assert form_put.validate(csrf=False) is True
    assert form_put._fields["title"].value == "Updated Title"

    # Test PATCH form submission
    environ_patch = {
        "REQUEST_METHOD": "PATCH",
        "PATH_INFO": "/item/1",
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": None,
    }
    req_patch = Request(environ_patch)
    req_patch.form = {"title": "Patched Title"}

    form_patch = Form(fields, req_patch)
    assert form_patch.validate(csrf=False) is True
    assert form_patch._fields["title"].value == "Patched Title"


def test_pluralize_filter_enhancements():
    # 2-argument form
    assert _pluralize_filter(1, "item", "items") == "item"
    assert _pluralize_filter(2, "item", "items") == "items"
    assert _pluralize_filter(0, "item", "items") == "items"

    # Single argument suffix form
    assert _pluralize_filter(1, "s") == ""
    assert _pluralize_filter(2, "s") == "s"

    # Irregular pluralization form
    assert _pluralize_filter(1, "y", "ies") == "y"
    assert _pluralize_filter(2, "y", "ies") == "ies"

    # Collections
    assert _pluralize_filter([1], "item", "items") == "item"
    assert _pluralize_filter([1, 2], "item", "items") == "items"
    assert _pluralize_filter([], "item", "items") == "items"
