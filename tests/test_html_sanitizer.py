"""Tests for HTML sanitizer to prevent Stored XSS in WYSIWYG fields."""

import pytest

from asok.utils.html_sanitizer import sanitize_html


class TestXSSPrevention:
    """Test that common XSS attack vectors are blocked."""

    def test_script_tag_removed(self):
        """Test that <script> tags are completely removed."""
        malicious = '<p>Hello</p><script>alert("XSS")</script><p>World</p>'
        result = sanitize_html(malicious)
        assert '<script>' not in result
        assert 'alert' not in result
        assert '<p>Hello</p>' in result
        assert '<p>World</p>' in result

    def test_onerror_attribute_removed(self):
        """Test that onerror event handlers are removed."""
        malicious = '<img src=x onerror="alert(1)">'
        result = sanitize_html(malicious)
        assert 'onerror' not in result
        assert 'alert' not in result

    def test_onclick_attribute_removed(self):
        """Test that onclick event handlers are removed."""
        malicious = '<a href="#" onclick="alert(1)">Click me</a>'
        result = sanitize_html(malicious)
        assert 'onclick' not in result
        assert 'alert' not in result
        assert '<a href="#">Click me</a>' in result

    def test_javascript_protocol_removed(self):
        """Test that javascript: protocol in links is removed."""
        malicious = '<a href="javascript:alert(1)">Click</a>'
        result = sanitize_html(malicious)
        assert 'javascript:' not in result.lower()
        assert 'alert' not in result

    def test_data_protocol_in_link_removed(self):
        """Test that data: protocol in links is removed."""
        malicious = '<a href="data:text/html,<script>alert(1)</script>">Click</a>'
        result = sanitize_html(malicious)
        assert 'data:' not in result or 'image/' in result  # data:image/* is allowed

    def test_style_tag_removed(self):
        """Test that <style> tags are removed."""
        malicious = '<style>body{background:url(javascript:alert(1))}</style>'
        result = sanitize_html(malicious)
        assert '<style>' not in result
        assert 'javascript' not in result.lower()

    def test_iframe_removed(self):
        """Test that <iframe> tags are removed."""
        malicious = '<iframe src="https://evil.com"></iframe>'
        result = sanitize_html(malicious)
        assert '<iframe' not in result
        assert 'evil.com' not in result

    def test_object_embed_removed(self):
        """Test that <object> and <embed> tags are removed."""
        malicious = '<object data="malicious.swf"></object><embed src="evil.swf">'
        result = sanitize_html(malicious)
        assert '<object' not in result
        assert '<embed' not in result


class TestAllowedContent:
    """Test that legitimate WYSIWYG content is preserved."""

    def test_basic_formatting_preserved(self):
        """Test that basic HTML formatting is preserved."""
        safe = '<p>Hello <strong>world</strong> with <em>emphasis</em></p>'
        result = sanitize_html(safe)
        assert '<p>Hello <strong>world</strong> with <em>emphasis</em></p>' in result

    def test_headings_preserved(self):
        """Test that heading tags are preserved."""
        safe = '<h1>Title</h1><h2>Subtitle</h2><p>Content</p>'
        result = sanitize_html(safe)
        assert '<h1>Title</h1>' in result
        assert '<h2>Subtitle</h2>' in result

    def test_lists_preserved(self):
        """Test that list tags are preserved."""
        safe = '<ul><li>Item 1</li><li>Item 2</li></ul>'
        result = sanitize_html(safe)
        assert '<ul>' in result
        assert '<li>Item 1</li>' in result
        assert '<li>Item 2</li>' in result
        assert '</ul>' in result

    def test_links_preserved(self):
        """Test that safe links are preserved."""
        safe = '<a href="https://example.com" title="Example">Link</a>'
        result = sanitize_html(safe)
        assert '<a href="https://example.com"' in result
        assert 'title="Example"' in result
        assert '>Link</a>' in result

    def test_images_preserved(self):
        """Test that safe images are preserved."""
        safe = '<img src="photo.jpg" alt="Photo" width="100" height="100">'
        result = sanitize_html(safe)
        assert '<img' in result
        assert 'src="photo.jpg"' in result
        assert 'alt="Photo"' in result

    def test_blockquote_preserved(self):
        """Test that blockquotes are preserved."""
        safe = '<blockquote>Quote</blockquote>'
        result = sanitize_html(safe)
        assert '<blockquote>Quote</blockquote>' in result

    def test_tables_preserved(self):
        """Test that table elements are preserved."""
        safe = '<table><tr><th>Header</th></tr><tr><td>Cell</td></tr></table>'
        result = sanitize_html(safe)
        assert '<table>' in result
        assert '<th>Header</th>' in result
        assert '<td>Cell</td>' in result


class TestStyleSanitization:
    """Test that style attributes are properly sanitized."""

    def test_safe_style_preserved(self):
        """Test that safe CSS is preserved."""
        safe = '<p style="color: red; font-size: 14px;">Text</p>'
        result = sanitize_html(safe)
        assert 'style=' in result
        assert 'color' in result or 'font-size' in result  # At least some style preserved

    def test_expression_in_style_removed(self):
        """Test that CSS expression() is removed."""
        malicious = '<p style="width: expression(alert(1));">Text</p>'
        result = sanitize_html(malicious)
        assert 'expression' not in result.lower()
        assert 'alert' not in result

    def test_javascript_in_style_removed(self):
        """Test that javascript in CSS is removed."""
        malicious = '<p style="background: url(javascript:alert(1));">Text</p>'
        result = sanitize_html(malicious)
        assert 'javascript' not in result.lower()


class TestEdgeCases:
    """Test edge cases and special inputs."""

    def test_empty_string(self):
        """Test that empty string returns empty string."""
        assert sanitize_html("") == ""
        assert sanitize_html(None) == ""

    def test_plain_text(self):
        """Test that plain text is escaped."""
        text = "Just plain text with <brackets>"
        result = sanitize_html(text)
        assert '&lt;brackets&gt;' in result or '<brackets>' not in result

    def test_html_comments_removed(self):
        """Test that HTML comments are removed."""
        malicious = '<p>Text</p><!-- Hidden comment --><p>More</p>'
        result = sanitize_html(malicious)
        assert '<!--' not in result
        assert 'Hidden comment' not in result

    def test_nested_tags(self):
        """Test that nested tags are handled correctly."""
        safe = '<div><p><strong>Nested <em>tags</em></strong></p></div>'
        result = sanitize_html(safe)
        assert '<div>' in result
        assert '<p>' in result
        assert '<strong>' in result
        assert '<em>' in result


class TestDataURIImages:
    """Test that data: URIs for images are handled correctly."""

    def test_data_image_allowed(self):
        """Test that data:image/* URIs are allowed."""
        safe = '<img src="data:image/png;base64,iVBORw0KG..." alt="Icon">'
        result = sanitize_html(safe)
        assert '<img' in result
        assert 'data:image/png' in result

    def test_data_non_image_blocked(self):
        """Test that data: URIs for non-images are blocked."""
        malicious = '<img src="data:text/html,<script>alert(1)</script>">'
        result = sanitize_html(malicious)
        # Should either remove the src or the entire img tag
        assert 'data:text/html' not in result or '<img' not in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
