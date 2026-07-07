import datetime

from asok import Field, Model, Relation
from asok.admin import Admin
from asok.orm import MODELS_REGISTRY
from asok.request import Request
from asok.templates import SafeString


class MockRenderable:
    def __init__(self, html):
        self.html = html

    def __str__(self):
        return SafeString(self.html)

    def __call__(self, **kwargs):
        attrs = " ".join(f'{k}="{v}"' for k, v in kwargs.items())
        if attrs:
            if ">" in self.html:
                tag, rest = self.html.split(">", 1)
                return MockRenderable(f"{tag} {attrs}>{rest}")
        return self


class MockFormF:
    def __init__(self, label, value=None, input_html="", choices=None):
        self._label = label
        self.value = value
        self.input_html = input_html
        self.choices = choices or []
        self._error = None

    @property
    def label(self):
        return MockRenderable(f"<label>{self._label}</label>")

    @property
    def input(self):
        return MockRenderable(self.input_html)


class DummyApp:
    def __init__(self, root_dir="/tmp"):
        self.config = {"AUTH_MODEL": "MockUser", "SECRET_KEY": "test-secret"}
        self.root_dir = root_dir
        self.models = []


class MockUser(Model):
    _db_path = ":memory:"
    __tablename__ = "mock_users"
    username = Field.String()
    is_admin = Field.Boolean(default=False)


def test_field_metadata_enrichment(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)
    admin_instance._registered["mock_users"] = {"model": MockUser}

    # Mock fields of different types
    date_field = Field.Date(label="Published Date")
    datetime_field = Field.DateTime(label="Published At")
    fk_field = Field.ForeignKey(MockUser, label="Author")
    text_field = Field.Text(label="Description")
    wysiwyg_field = Field.Text(label="Content", wysiwyg=True)
    bool_field = Field.Boolean(label="Is Published")
    int_bool_field = Field.Integer(label="Is Active")

    readonly_set = set()

    # Check that is_date metadata is set
    tup_date, meta_date = admin_instance._field_meta(
        "pub_date", date_field, readonly_set
    )
    assert meta_date["is_date"] is True
    assert meta_date["is_datetime"] is False

    # Check that is_datetime metadata is set
    tup_dt, meta_dt = admin_instance._field_meta("pub_at", datetime_field, readonly_set)
    assert meta_dt["is_datetime"] is True
    assert meta_dt["is_date"] is False

    # Check that foreign key metadata is set
    tup_fk, meta_fk = admin_instance._field_meta("author_id", fk_field, readonly_set)
    assert meta_fk["is_fk"] is True
    assert meta_fk["fk_model_slug"] == "mock_users"

    # Check that text metadata is set
    tup_txt, meta_txt = admin_instance._field_meta(
        "description", text_field, readonly_set
    )
    assert meta_txt["is_text"] is True
    assert meta_txt["wysiwyg"] is False

    # Check that wysiwyg metadata is set
    tup_wys, meta_wys = admin_instance._field_meta(
        "content", wysiwyg_field, readonly_set
    )
    assert meta_wys["is_text"] is True
    assert meta_wys["wysiwyg"] is True

    # Check that boolean metadata is set
    tup_bool, meta_bool = admin_instance._field_meta(
        "is_published", bool_field, readonly_set
    )
    assert meta_bool["is_boolean"] is True

    # Check that integer fields matching is_* / has_* are recognized as boolean
    tup_int_bool, meta_int_bool = admin_instance._field_meta(
        "is_active", int_bool_field, readonly_set
    )
    assert meta_int_bool["is_boolean"] is True


def test_detail_template_rendering(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)

    # Build mock request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/mock/1/view",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.user = MockUser(username="admin", is_admin=True)

    class MockItem:
        id = 123
        description = "This is a multiline\ndescription field."
        body_content = SafeString("<p>This is <strong>WYSIWYG</strong> content.</p>")
        title = "A beautiful post"
        published_on = "2026-05-25"
        created_at_str = "2026-06-12 20:52:14"
        created_at_dt = datetime.datetime(2026, 6, 12, 20, 52, 14)
        is_published = 1
        image = "/uploads/2eb68e7a-d90d-4745-9381-425d1e38a257.jpeg"

        class MockAuthor:
            id = 456

            def __str__(self):
                return "John Doe"

        author_id = MockAuthor()

        def __getitem__(self, name):
            return getattr(self, name)

    item = MockItem()

    # Let's mock field_groups that would be passed to template
    field_groups = [
        {
            "label": "General",
            "fields": [
                # Text Field
                {
                    "f": MockFormF(
                        label="Description",
                        value=item.description,
                        input_html='<textarea name="description">This is a multiline\ndescription field.</textarea>',
                    ),
                    "m": {
                        "name": "description",
                        "is_text": True,
                        "wysiwyg": False,
                    },
                },
                # WYSIWYG Field
                {
                    "f": MockFormF(
                        label="Body Content", value=item.body_content, input_html=""
                    ),
                    "m": {
                        "name": "body_content",
                        "is_text": True,
                        "wysiwyg": True,
                    },
                },
                # Normal Field (string)
                {
                    "f": MockFormF(
                        label="Title",
                        value=item.title,
                        input_html='<input name="title" type="text" value="A beautiful post">',
                    ),
                    "m": {
                        "name": "title",
                        "is_text": False,
                        "wysiwyg": False,
                    },
                },
                # Date Field
                {
                    "f": MockFormF(
                        label="Published On",
                        value=item.published_on,
                        input_html='<input name="published_on" type="date" value="2026-05-25">',
                    ),
                    "m": {
                        "name": "published_on",
                        "is_date": True,
                    },
                },
                # Datetime Field (ISO string)
                {
                    "f": MockFormF(
                        label="Created At String",
                        value=item.created_at_str,
                        input_html='<input name="created_at_str" type="text" value="2026-06-12 20:52:14">',
                    ),
                    "m": {
                        "name": "created_at_str",
                        "is_datetime": True,
                    },
                },
                # Datetime Field (Datetime object)
                {
                    "f": MockFormF(
                        label="Created At Object",
                        value=item.created_at_dt,
                        input_html='<input name="created_at_dt" type="text" value="2026-06-12 20:52:14">',
                    ),
                    "m": {
                        "name": "created_at_dt",
                        "is_datetime": True,
                    },
                },
                # FK Field
                {
                    "f": MockFormF(
                        label="Author",
                        value=item.author_id,
                        input_html='<select name="author_id"><option value="456" selected>John Doe</option></select>',
                    ),
                    "m": {
                        "name": "author_id",
                        "is_fk": True,
                        "fk_model_slug": "users",
                        "fk_rel_name": "author_id",
                    },
                },
                # Boolean Field (Custom)
                {
                    "f": MockFormF(
                        label="Is Published",
                        value=item.is_published,
                        input_html='<input name="is_published" type="checkbox" checked>',
                    ),
                    "m": {
                        "name": "is_published",
                        "is_boolean": True,
                    },
                },
                # Image Field
                {
                    "f": MockFormF(label="Image", value=item.image, input_html=""),
                    "m": {
                        "name": "image",
                        "is_file": True,
                        "is_image": True,
                        "file_value": item.image,
                    },
                },
            ],
        }
    ]

    # Call _render on admin_instance to render detail.html
    html_content = admin_instance._render(
        req,
        "detail.html",
        item=item,
        field_groups=field_groups,
        m2m_fields=[],
        inlines=[],
        title="View Item",
        can_edit=True,
        can_delete=True,
        slug="posts",
        breadcrumbs=[{"label": "Dashboard", "url": "/admin"}],
    )

    # 1. Verify CSS classes/elements are present
    assert 'class="detail-view"' in html_content

    # 2. Verify that Text & WYSIWYG fields have style="grid-column: 1 / -1"
    assert 'id="fg-description" style="grid-column: 1 / -1"' in html_content
    assert 'id="fg-body_content" style="grid-column: 1 / -1"' in html_content

    # 3. Verify WYSIWYG content rendered safely (SafeString)
    assert (
        '<div class="detail-wysiwyg-content"><p>This is <strong>WYSIWYG</strong> content.</p></div>'
        in html_content
    )

    # 4. Verify text field renders as textarea and contains the text
    assert "<textarea" in html_content
    assert "This is a multiline\ndescription field." in html_content

    # 5. Verify Foreign Key value is rendered
    assert "John Doe" in html_content

    # 6. Verify datetime string/object rendering
    assert "2026-06-12 20:52:14" in html_content

    # 7. Verify Boolean field renders as checkbox
    assert 'type="checkbox"' in html_content
    assert "checked" in html_content

    # 8. Verify Image field renders with a thumbnail image preview and link
    assert 'class="detail-image-thumb"' in html_content
    assert 'src="/uploads/2eb68e7a-d90d-4745-9381-425d1e38a257.jpeg"' in html_content
    assert 'href="/uploads/2eb68e7a-d90d-4745-9381-425d1e38a257.jpeg"' in html_content


class MockRelated(Model):
    _db_path = ":memory:"
    __tablename__ = "mock_related"
    name = Field.String()


class MockModelWithRelation(Model):
    _db_path = ":memory:"
    __tablename__ = "mock_model_with_relation"
    related_id = Field.ForeignKey(MockRelated)

    # Establish relation
    related = Relation.BelongsTo("MockRelated", foreign_key="related_id")


def test_fk_rel_name_in_field_metadata(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)
    admin_instance._registered["mock_model_with_relation"] = {
        "model": MockModelWithRelation
    }
    MODELS_REGISTRY["MockRelated"] = MockRelated

    readonly_set = set()
    tup_fk, meta_fk = admin_instance._field_meta(
        "related_id",
        MockModelWithRelation._fields["related_id"],
        readonly_set,
        model=MockModelWithRelation,
    )
    assert meta_fk["is_fk"] is True
    assert meta_fk["fk_rel_name"] == "related"


def test_display_filter_and_detail_rendering(tmp_path):
    app = DummyApp(root_dir=str(tmp_path))
    admin_instance = Admin(app)
    MODELS_REGISTRY["MockRelated"] = MockRelated
    MODELS_REGISTRY["MockModelWithRelation"] = MockModelWithRelation

    # Build mock request
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/admin/mock/1/view",
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "wsgi.input": None,
    }
    req = Request(environ)
    req.user = MockUser(username="admin", is_admin=True)

    class MockItemWithFK:
        id = 111
        related_id = 999  # Raw value is integer ID

        class MockRelatedItem:
            id = 999
            name = "Awesome Product"

            def __str__(self):
                return self.name

        related = MockRelatedItem()  # Relationship property returns related object

        def __getitem__(self, name):
            return getattr(self, name)

    item = MockItemWithFK()

    field_groups = [
        {
            "label": "Relations",
            "fields": [
                {
                    "f": MockFormF(
                        label="Related Item",
                        value=999,
                        input_html='<select name="related_id"><option value="999" selected>Awesome Product</option></select>',
                    ),
                    "m": {
                        "name": "related_id",
                        "is_fk": True,
                        "fk_model_slug": "mock_related",
                        "fk_rel_name": "related",
                    },
                }
            ],
        }
    ]

    html_content = admin_instance._render(
        req,
        "detail.html",
        item=item,
        field_groups=field_groups,
        m2m_fields=[],
        inlines=[],
        title="View Item",
        can_edit=True,
        can_delete=True,
        slug="mock_model_with_relation",
        breadcrumbs=[{"label": "Dashboard", "url": "/admin"}],
    )

    assert "Awesome Product" in html_content
