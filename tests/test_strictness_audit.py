import pytest

from asok.core import Asok
from asok.core._asset_injector import precompile_directives
from asok.forms import Form
from asok.orm import Field, Model
from asok.ws.live import on_live_message


class StrictUser(Model):
    name = Field.String()
    email = Field.String()


def test_model_unexpected_field_raises():
    with pytest.raises(TypeError) as exc:
        StrictUser(emaiil="test@example.com")
    assert "StrictUser' got unexpected field(s): emaiil" in str(exc.value)

    with pytest.raises(TypeError) as exc:
        StrictUser.create(nme="John")
    assert "StrictUser' got unexpected field(s): nme" in str(exc.value)


def test_model_update_unexpected_field_raises(tmp_path):
    user = StrictUser(_trust=True, id=1, name="Alice", email="alice@test.com")
    with pytest.raises(AttributeError) as exc:
        user.update(emaiil="new@test.com")
    assert "'StrictUser' has no field 'emaiil'" in str(exc.value)


def test_form_strict_error_queries():
    form = Form({"username": Form.text("Username")})
    # Valid field has no error
    assert form.error("username") == ""
    assert form.has_error("username") is False

    # Typo field raises KeyError
    with pytest.raises(KeyError) as exc:
        form.error("usernme")
    assert "Form has no field 'usernme'" in str(exc.value)

    with pytest.raises(KeyError) as exc:
        form.has_error("usernme")
    assert "Form has no field 'usernme'" in str(exc.value)


def test_form_attribute_access_strict():
    form = Form({"username": Form.text("Username")})
    assert form.username.name == "username"

    # Typo field raises AttributeError
    with pytest.raises(AttributeError):
        _ = form.usernme

    # Accessing SchemaMixin method that is NOT a field on the form raises AttributeError
    with pytest.raises(AttributeError) as exc:
        _ = form.email
    assert "'Form' has no field 'email'" in str(exc.value)


def test_directive_asok_for_syntax_validation():
    app = Asok()
    # Invalid syntax without ' in '
    with pytest.raises(ValueError) as exc:
        precompile_directives(app, '<div asok-for="item of items"></div>')
    assert "Invalid asok-for expression 'item of items'" in str(exc.value)

    # Empty expression in directive
    with pytest.raises(ValueError) as exc:
        precompile_directives(app, '<div asok-text=""></div>')
    assert "cannot have an empty expression" in str(exc.value)


def test_ws_live_component_strict_method_call():
    from asok.component import Component, exposed

    class LiveCounter(Component):
        count: int = 0

        @exposed
        def increment(self):
            self.count += 1

        def secret_action(self):
            pass

    server = Asok()
    server.secret_key = "test-secret-key-1234567890123456"

    class MockConn:
        def __init__(self):
            self.sent = []
            self.path = "/"
            self.headers = {}
            self.user = None
            self.session = None
            self._live_comps = {}

        def send_json(self, payload):
            self.sent.append(payload)

    conn = MockConn()
    comp = LiveCounter()
    signed_state = comp._sign_state(server.secret_key)
    conn._live_comps["c123"] = (LiveCounter, signed_state)

    # Calling typo method raises AttributeError and sends error JSON
    call_msg = '{"op": "call", "cid": "c123", "method": "incremnt", "val": null}'
    on_live_message(server, conn, call_msg)
    assert len(conn.sent) == 1
    assert conn.sent[0]["op"] == "error"
    assert "has no method 'incremnt'" in conn.sent[0]["message"]

    # Calling unexposed method raises PermissionError and sends error JSON
    conn.sent.clear()
    unexposed_msg = (
        '{"op": "call", "cid": "c123", "method": "secret_action", "val": null}'
    )
    on_live_message(server, conn, unexposed_msg)
    assert len(conn.sent) == 1
    assert conn.sent[0]["op"] == "error"
    assert "is not decorated with @exposed" in conn.sent[0]["message"]


def test_validator_unknown_rule_raises():
    from asok.validation import Validator

    v = Validator({"name": "test"})
    with pytest.raises(ValueError) as exc:
        v.rule("name", "requird")
    assert "Unknown validation rule 'requird'" in str(exc.value)


def test_validator_missing_rule_argument_raises():
    from asok.validation import Validator

    v = Validator({"name": "test"})
    with pytest.raises(ValueError) as exc:
        v.rule("name", "min")
    assert "Rule 'min' requires an argument" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        v.rule("name", "between:5")
    assert "Rule 'between' requires two bounds" in str(exc.value)


def test_validator_unique_missing_model_raises():
    from asok.validation import Validator

    v = Validator({"email": "test@example.com"})
    with pytest.raises(ValueError) as exc:
        v.rule("email", "unique:NoSuchModel,email")
    assert "Model 'NoSuchModel' not found in registry" in str(exc.value)


def test_schema_load_non_dict_raises():
    from asok.validation import Schema

    class UserSchema(Schema):
        pass

    schema = UserSchema()
    with pytest.raises(TypeError) as exc:
        schema.load("invalid_input")
    assert "Schema.load() expects a dict" in str(exc.value)


def test_request_validate_invalid_type_raises():
    from asok.request import Request

    req = Request({"REQUEST_METHOD": "GET", "PATH_INFO": "/"})
    with pytest.raises(TypeError) as exc:
        req.validate(["email", "required"])
    assert "request.validate() expects a dict of rules or a Form class" in str(
        exc.value
    )


def test_request_validate_malformed_json_raises():
    import io

    from asok.exceptions import ValidationError
    from asok.request import Request

    payload = b'{"name": '  # invalid incomplete JSON
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(payload)),
        "wsgi.input": io.BytesIO(payload),
    }
    req = Request(environ)
    with pytest.raises(ValidationError) as exc:
        req.validate({"name": "required"}, csrf=False)
    assert "Malformed JSON in request body" in str(exc.value)


def test_request_validate_non_dict_json_raises():
    import io

    from asok.exceptions import ValidationError
    from asok.request import Request

    payload = b"[1, 2, 3]"  # valid JSON but a list, not an object
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api",
        "CONTENT_TYPE": "application/json",
        "CONTENT_LENGTH": str(len(payload)),
        "wsgi.input": io.BytesIO(payload),
    }
    req = Request(environ)
    with pytest.raises(ValidationError) as exc:
        req.validate({"name": "required"}, csrf=False)
    assert "Expected a JSON object (dict) in request body" in str(exc.value)


def test_request_shared_form_not_found_raises():
    from asok.request import Request

    req = Request({"REQUEST_METHOD": "GET", "PATH_INFO": "/"})
    with pytest.raises(KeyError) as exc:
        req.shared_form("unknown_form")
    assert "Shared form 'unknown_form' not found" in str(exc.value)


def test_request_shared_callable_exception_propagates():
    from asok.request import Request

    def bad_shared():
        raise RuntimeError("Something went wrong in user shared factory")

    app = Asok()
    app.share(broken=bad_shared)

    req = Request({"REQUEST_METHOD": "GET", "PATH_INFO": "/", "asok.app": app})
    with pytest.raises(RuntimeError) as exc:
        _ = req.shared("broken")
    assert "Something went wrong in user shared factory" in str(exc.value)


def test_request_authenticate_missing_model_raises():
    from asok.request import Request

    app = Asok()
    app.config["AUTH_MODEL"] = "NonExistentUser"
    req = Request({"REQUEST_METHOD": "POST", "PATH_INFO": "/login", "asok.app": app})

    with pytest.raises(ValueError) as exc:
        req.authenticate("password", email="test@example.com", password="pwd")
    assert "Authentication model 'NonExistentUser' not found" in str(exc.value)


def test_request_cookies_accessors():
    from asok.request import Request

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/",
        "HTTP_COOKIE": "theme=dark; session_test=xyz",
    }
    req = Request(environ)
    assert req.cookies.get("theme") == "dark"
    assert req.cookie("theme") == "dark"
    assert req.cookie("missing", "default_val") == "default_val"


def test_query_latest_oldest_invalid_column_raises():
    with pytest.raises(ValueError) as exc:
        StrictUser.query().latest("typo_column")
    assert "Invalid column: typo_column" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        StrictUser.query().oldest("typo_column")
    assert "Invalid column: typo_column" in str(exc.value)


def test_orm_relation_missing_model_raises():
    from asok.orm import Relation

    class Book(Model):
        title = Field.String()
        author = Relation.BelongsTo("NoSuchAuthorModel")
        reviews = Relation.HasMany("NoSuchReviewModel")

    b = Book(_trust=True, id=1, title="Test", nosuchauthormodel_id=10)

    with pytest.raises(ValueError) as exc:
        _ = b.author
    assert (
        "Related model 'NoSuchAuthorModel' for relation 'author' on Book was not found"
        in str(exc.value)
    )

    with pytest.raises(ValueError) as exc:
        _ = b.reviews
    assert (
        "Related model 'NoSuchReviewModel' for relation 'reviews' on Book was not found"
        in str(exc.value)
    )


def test_orm_foreign_key_missing_model_raises():
    class Item(Model):
        name = Field.String()
        owner_id = Field.ForeignKey("NoSuchOwnerModel")

    item = Item(_trust=True, id=1, name="Tool", owner_id=99)
    with pytest.raises(ValueError) as exc:
        _ = item.owner
    assert (
        "Related model 'NoSuchOwnerModel' for foreign key 'owner_id' on Item was not found"
        in str(exc.value)
    )


def test_orm_unknown_relation_type_raises():
    from asok.orm import Relation

    with pytest.raises(ValueError) as exc:

        class BrokenModel(Model):
            custom = Relation("InvalidRelationType", "TargetModel")

    assert "Unknown relation type 'InvalidRelationType'" in str(exc.value)


def test_ws_live_unknown_op_sends_error():
    server = Asok()
    server.secret_key = "test-secret-key-1234567890123456"

    class MockConn:
        def __init__(self):
            self.sent = []
            self.path = "/"
            self.headers = {}
            self.user = None
            self.session = None
            self._live_comps = {}

        def send_json(self, payload):
            self.sent.append(payload)

    conn = MockConn()
    on_live_message(server, conn, '{"op": "non_existent_operation", "cid": "c1"}')
    assert len(conn.sent) == 1
    assert conn.sent[0]["op"] == "error"
    assert "Unknown live operation: 'non_existent_operation'" in conn.sent[0]["message"]


def test_ws_live_unknown_component_join_sends_error():
    server = Asok()
    server.secret_key = "test-secret-key-1234567890123456"

    class MockConn:
        def __init__(self):
            self.sent = []
            self.path = "/"
            self.headers = {}
            self.user = None
            self.session = None
            self._live_comps = {}

        def send_json(self, payload):
            self.sent.append(payload)

    conn = MockConn()
    join_msg = '{"op": "join", "cid": "c1", "name": "NonExistentComponent", "state": "signed_val"}'
    on_live_message(server, conn, join_msg)
    assert len(conn.sent) == 1
    assert conn.sent[0]["op"] == "error"
    assert (
        "Component 'NonExistentComponent' not found in COMPONENTS_REGISTRY"
        in conn.sent[0]["message"]
    )


def test_ws_live_call_missing_cid_sends_error():
    server = Asok()
    server.secret_key = "test-secret-key-1234567890123456"

    class MockConn:
        def __init__(self):
            self.sent = []
            self.path = "/"
            self.headers = {}
            self.user = None
            self.session = None
            self._live_comps = {}

        def send_json(self, payload):
            self.sent.append(payload)

    conn = MockConn()
    call_msg = (
        '{"op": "call", "cid": "c_missing", "method": "do_something", "val": null}'
    )
    on_live_message(server, conn, call_msg)
    assert len(conn.sent) == 1
    assert conn.sent[0]["op"] == "error"
    assert (
        "Live component 'c_missing' not found on this connection"
        in conn.sent[0]["message"]
    )
