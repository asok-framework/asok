import unittest
from unittest.mock import MagicMock, patch

from asok import (
    authenticate,
    back,
    current_request,
    current_user,
    flash,
    login,
    logout,
    render_block,
    render_blocks,
    render_template,
)
from asok.context import request_context
from asok.request import Request


class TestShortcuts(unittest.TestCase):
    def setUp(self):
        self.environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/test",
            "SERVER_NAME": "localhost",
            "SERVER_PORT": "8000",
            "wsgi.url_scheme": "http",
            "HTTP_REFERER": "http://localhost:8000/previous",
        }
        self.request = Request(self.environ)

    def test_shortcuts_with_active_request(self):
        with request_context(self.request):
            # Test current_request access
            self.assertEqual(current_request.path, "/test")

            # Test flash
            flash("Welcome to Asok!", "success")
            messages = self.request.get_flashed_messages()
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].category, "success")
            self.assertEqual(messages[0].message, "Welcome to Asok!")

            # Test back (raises RedirectException)
            with self.assertRaises(Exception) as ctx:
                back(default="/home")
            self.assertIn("RedirectException", type(ctx.exception).__name__)

            # Test current_user
            self.assertFalse(bool(current_user))
            self.assertEqual(repr(current_user), "<AnonymousUser>")

            # Test login / logout
            mock_user = MagicMock()
            mock_user.id = 42
            mock_user.username = "alice"
            login(mock_user)
            self.assertTrue(bool(current_user))
            self.assertEqual(current_user.id, 42)
            self.assertEqual(current_user.username, "alice")

            logout()
            self.assertFalse(bool(current_user))

            # Test authenticate delegation
            with patch.object(
                self.request, "authenticate", return_value=mock_user
            ) as mock_auth:
                user = authenticate(email="alice@example.com", password="secret")
                mock_auth.assert_called_once_with(
                    password_field="password",
                    email="alice@example.com",
                    password="secret",
                )
                self.assertEqual(user, mock_user)

            # Test render_template delegation
            with patch.object(
                self.request, "html", return_value="<h1>OK</h1>"
            ) as mock_html:
                res = render_template("index.html", title="Home")
                mock_html.assert_called_once_with("index.html", title="Home")
                self.assertEqual(res, "<h1>OK</h1>")

            # Test render_block delegation
            with patch.object(
                self.request, "block", return_value="<div>Block</div>"
            ) as mock_block:
                b1 = render_block("index.html", "main")
                self.assertEqual(b1, "<div>Block</div>")
                mock_block.assert_called_once_with("index.html", block_name="main")

            # Test render_blocks delegation
            with patch.object(
                self.request, "blocks", return_value="<div>Blocks</div>"
            ) as mock_blocks:
                bs1 = render_blocks("index.html", ["header", "footer"])
                self.assertEqual(bs1, "<div>Blocks</div>")
                mock_blocks.assert_called_once_with("index.html", ["header", "footer"])

    def test_request_template_aliases(self):
        self.assertTrue(hasattr(self.request, "render_template"))
        self.assertTrue(hasattr(self.request, "render_block"))
        self.assertTrue(hasattr(self.request, "render_blocks"))
        self.assertEqual(self.request.render_template, self.request.html)
        self.assertEqual(self.request.render_block, self.request.block)
        self.assertEqual(self.request.render_blocks, self.request.blocks)
