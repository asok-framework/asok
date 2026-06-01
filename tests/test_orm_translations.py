from asok import Field, Model
from asok.context import request_context
from asok.request.request import Request


# Style B: All suffixed fields (title_fr, title_en)
class TranslatablePost(Model):
    __tablename__ = "translatable_posts"

    title_fr = Field.String()
    title_en = Field.String()
    content_fr = Field.Text()
    content_es = Field.Text()


# Style A: Base field (title) + translation fields (title_fr)
class StyleAPost(Model):
    __tablename__ = "style_a_posts"

    title = Field.String()  # Base column, defaults to English
    title_fr = Field.String()
    content = Field.Text()
    content_es = Field.Text()


def test_translatable_property_generation():
    # Verify that properties 'title' and 'content' were automatically created for Style B
    assert hasattr(TranslatablePost, "title")
    assert hasattr(TranslatablePost, "content")
    assert isinstance(getattr(TranslatablePost, "title"), property)
    assert isinstance(getattr(TranslatablePost, "content"), property)
    # Verify we did not generate property for 'title_fr' (it's not on the class, only on instances)
    assert not hasattr(TranslatablePost, "title_fr")


def test_translatable_getter_default_en():
    # Outside request context, defaults to 'en'
    post = TranslatablePost(title_fr="Bonjour", title_en="Hello")
    assert post.title == "Hello"


def test_translatable_setter_default_en():
    post = TranslatablePost(title_en="Hello")
    post.title = "Hi"
    assert post.title_en == "Hi"


def test_translatable_getter_with_request_context():
    post = TranslatablePost(
        title_fr="Bonjour",
        title_en="Hello",
        content_fr="Contenu FR",
        content_es="Contenido ES",
    )

    # 1. Test French context
    environ_fr = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "fr-FR,fr;q=0.9",
    }
    req_fr = Request(environ_fr)

    with request_context(req_fr):
        assert post.title == "Bonjour"
        assert post.content == "Contenu FR"

    # 2. Test English context
    environ_en = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "en-US,en;q=0.9",
    }
    req_en = Request(environ_en)

    with request_context(req_en):
        assert post.title == "Hello"
        # Since 'content_en' is not defined, should fallback to default ('fr')
        assert post.content == "Contenu FR"


def test_translatable_setter_with_request_context():
    post = TranslatablePost()

    # Test French context
    environ_fr = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "fr",
    }
    req_fr = Request(environ_fr)

    with request_context(req_fr):
        post.title = "Allo"

    # Test English context
    environ_en = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "en",
    }
    req_en = Request(environ_en)

    with request_context(req_en):
        post.title = "Hi"

    assert post.title_fr == "Allo"
    assert post.title_en == "Hi"


def test_translatable_fallbacks():
    # Only Spanish content is defined
    post = TranslatablePost(content_es="Hola")

    # In French context, should fallback to Spanish (first non-empty translation)
    environ_fr = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "fr",
    }
    req_fr = Request(environ_fr)

    with request_context(req_fr):
        assert post.content == "Hola"


# --- STYLE A TESTS ---


def test_style_a_property_generation():
    # Verify that 'title' property is created on class (overriding the popped Field object)
    assert hasattr(StyleAPost, "title")
    assert isinstance(getattr(StyleAPost, "title"), property)
    assert not hasattr(StyleAPost, "title_fr")


def test_style_a_getter_setter_default():
    post = StyleAPost(title="Hello Base", title_fr="Bonjour")
    # Outside context, defaults to base field
    assert post.title == "Hello Base"

    # Outside context, setter writes to base field
    post.title = "Hi Base"
    assert post.title == "Hi Base"
    assert post.title_fr == "Bonjour"


def test_style_a_getter_setter_with_context():
    post = StyleAPost(title="Hello Base", title_fr="Bonjour")

    # 1. French Context
    environ_fr = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "fr",
    }
    req_fr = Request(environ_fr)

    with request_context(req_fr):
        assert post.title == "Bonjour"
        post.title = "Salut"

    assert post.title_fr == "Salut"

    # 2. English Context (uses base field because title_en is not defined)
    environ_en = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "en",
    }
    req_en = Request(environ_en)

    with request_context(req_en):
        assert post.title == "Hello Base"
        post.title = "Hey Base"

    # Verify that base field got updated
    assert post.title == "Hey Base"
    assert post.title_fr == "Salut"


def test_style_a_trust_initialization():
    # Verify that when loading from DB (using _trust=True), base field is not routed to translation field
    environ_fr = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "fr",
    }
    req_fr = Request(environ_fr)

    with request_context(req_fr):
        # Even inside a French context, instantiating with _trust=True writes directly to raw fields
        post = StyleAPost(_trust=True, title="Hello DB", title_fr="Bonjour DB")
        assert post.title_fr == "Bonjour DB"
        # Bypassed setter correctly, so base field got populated directly
        assert (
            post.title == "Bonjour DB"
        )  # Under fr context, post.title resolves to post.title_fr!
        # Accessing raw dictionary to verify both are stored correctly
        assert post.__dict__["title"] == "Hello DB"
        assert post.__dict__["title_fr"] == "Bonjour DB"


def test_style_a_custom_locale():
    # Setup a mock application with LOCALE = "fr"
    class MockApp:
        config = {"LOCALE": "fr"}

    app_instance = MockApp()

    # Create post where base field represents French, and we have an English suffix
    class StyleAFrenchPost(Model):
        __tablename__ = "style_a_french_posts"
        title = Field.String()  # Holds French because LOCALE is 'fr'
        title_en = Field.String()  # Translation

    post = StyleAFrenchPost(title="Bonjour Base", title_en="Hello Translation")

    # 1. French context (should resolve to the base field 'title')
    environ_fr = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "fr",
        "asok.app": app_instance,
    }
    req_fr = Request(environ_fr)

    with request_context(req_fr):
        assert post.title == "Bonjour Base"
        post.title = "Salut Base"

    assert post.__dict__["title"] == "Salut Base"

    # 2. English context (should resolve to 'title_en')
    environ_en = {
        "PATH_INFO": "/",
        "REQUEST_METHOD": "GET",
        "HTTP_ACCEPT_LANGUAGE": "en",
        "asok.app": app_instance,
    }
    req_en = Request(environ_en)

    with request_context(req_en):
        assert post.title == "Hello Translation"
        post.title = "Hi Translation"

    assert post.title_en == "Hi Translation"
    assert post.__dict__["title"] == "Salut Base"
