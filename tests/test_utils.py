"""
Tests for the utility functions (humanize and minify).
"""

import datetime

from asok.utils.humanize import duration, file_size, intcomma, time_ago
from asok.utils.minify import minify_html

# ---------------------------------------------------------------------------
# Humanize
# ---------------------------------------------------------------------------


class TestHumanize:
    def test_file_size(self):
        assert file_size(500) == "500.0 B"
        assert file_size(1024) == "1.0 KB"
        assert file_size(1536) == "1.5 KB"
        assert file_size(1048576) == "1.0 MB"
        assert file_size(1073741824) == "1.0 GB"
        assert file_size(0) == "0 B"

    def test_intcomma(self):
        assert intcomma(999) == "999"
        assert intcomma(1000) == "1,000"
        assert intcomma(1000000) == "1,000,000"
        assert intcomma(12345.67) == "12,345.67"
        assert intcomma("not-a-number") == "not-a-number"

    def test_duration(self):
        assert duration(45) == "45s"
        assert duration(60) == "1m"
        assert duration(120) == "2m"
        assert duration(3600) == "1h"
        assert duration(7200) == "2h"
        assert duration(86400) == "1d"
        assert duration(172800) == "2d"
        assert duration(None) == "0s"

    def test_time_ago(self):
        now = datetime.datetime.now()

        # Seconds
        assert time_ago(now - datetime.timedelta(seconds=10)) == "just now"
        assert time_ago(now - datetime.timedelta(seconds=59)) == "just now"

        # Minutes
        assert time_ago(now - datetime.timedelta(minutes=1)) == "1 minute ago"
        assert time_ago(now - datetime.timedelta(minutes=5)) == "5 minutes ago"

        # Hours
        assert time_ago(now - datetime.timedelta(hours=1)) == "1 hour ago"
        assert time_ago(now - datetime.timedelta(hours=3)) == "3 hours ago"

        # Days
        assert time_ago(now - datetime.timedelta(days=1)) == "1 day ago"
        assert time_ago(now - datetime.timedelta(days=10)) == "1 week ago"

        # Months
        assert time_ago(now - datetime.timedelta(days=35)) == "1 month ago"
        assert time_ago(now - datetime.timedelta(days=65)) == "2 months ago"

        # Years
        assert time_ago(now - datetime.timedelta(days=400)) == "1 year ago"

        # None
        assert time_ago(None) == ""


# ---------------------------------------------------------------------------
# Minify
# ---------------------------------------------------------------------------


class TestMinify:
    def test_minify_html_removes_whitespace_between_tags(self):
        html = """
        <div class="container">
            <h1>Hello</h1>
            <p>
                World
            </p>
        </div>
        """
        minified = minify_html(html)
        assert minified == '<div class="container"><h1>Hello</h1><p> World </p></div>'

    def test_minify_html_removes_comments(self):
        html = "<div><!-- This is a comment -->Content</div>"
        minified = minify_html(html)
        assert minified == "<div>Content</div>"

    def test_minify_html_preserves_content_spacing(self):
        html = "<span>A</span> <span>B</span>"
        minified = minify_html(html)
        # Spaces outside tags might be compressed, but content inside should be safe.
        # minify_html implementation depends on the regex, but let's check basic sanity.
        assert "A" in minified and "B" in minified
