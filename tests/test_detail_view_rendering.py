from asok import Field, Model
from asok.admin import Admin
from asok.request import Request
from asok.templates import SafeString


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

    readonly_set = set()

    # Check that is_date metadata is set
    tup_date, meta_date = admin_instance._field_meta("pub_date", date_field, readonly_set)
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
    tup_txt, meta_txt = admin_instance._field_meta("description", text_field, readonly_set)
    assert meta_txt["is_text"] is True
    assert meta_txt["wysiwyg"] is False

    # Check that wysiwyg metadata is set
    tup_wys, meta_wys = admin_instance._field_meta("content", wysiwyg_field, readonly_set)
    assert meta_wys["is_text"] is True
    assert meta_wys["wysiwyg"] is True


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

    # Let's mock field_groups that would be passed to template
    field_groups = [
        {
            "label": "General",
            "fields": [
                # Text Field
                {
                    "f": type("MockFormF", (), {"label": "Description"})(),
                    "m": {
                        "name": "description",
                        "is_text": True,
                        "wysiwyg": False,
                    }
                },
                # WYSIWYG Field
                {
                    "f": type("MockFormF", (), {"label": "Body Content"})(),
                    "m": {
                        "name": "body_content",
                        "is_text": True,
                        "wysiwyg": True,
                    }
                },
                # Normal Field (string)
                {
                    "f": type("MockFormF", (), {"label": "Title"})(),
                    "m": {
                        "name": "title",
                        "is_text": False,
                        "wysiwyg": False,
                    }
                },
                # Date Field
                {
                    "f": type("MockFormF", (), {"label": "Published On"})(),
                    "m": {
                        "name": "published_on",
                        "is_date": True,
                    }
                },
                # FK Field
                {
                    "f": type("MockFormF", (), {"label": "Author"})(),
                    "m": {
                        "name": "author_id",
                        "is_fk": True,
                        "fk_model_slug": "users",
                    }
                }
            ]
        }
    ]

    class MockItem:
        id = 123
        description = "This is a multiline\ndescription field."
        body_content = SafeString("<p>This is <strong>WYSIWYG</strong> content.</p>")
        title = "A beautiful post"
        published_on = "2026-05-25"

        class MockAuthor:
            id = 456
            def __str__(self):
                return "John Doe"

        author_id = MockAuthor()

        def __getitem__(self, name):
            return getattr(self, name)

    item = MockItem()

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

    # 1. Verify CSS styles for detail-wysiwyg-content are present
    assert ".detail-wysiwyg-content" in html_content
    assert "list-style-type: disc" in html_content
    assert "line-height: 1.6" in html_content

    # 2. Verify that Text & WYSIWYG fields have style="grid-column: 1 / -1"
    # Search for description div and check layout style
    assert 'class="detail-field" style="grid-column: 1 / -1"' in html_content

    # 3. Verify WYSIWYG content rendered safely (SafeString)
    assert '<div class="detail-wysiwyg-content"><p>This is <strong>WYSIWYG</strong> content.</p></div>' in html_content

    # 4. Verify text field preserves line breaks with white-space: pre-wrap
    assert '<div style="white-space: pre-wrap">This is a multiline\ndescription field.</div>' in html_content

    # 5. Verify Foreign Key link is rendered correctly
    assert 'href="/admin/users/456/view"' in html_content
    assert 'John Doe' in html_content
