import os
import re
from html import escape

from asok import Request
from asok.templates import render_template_string
from models.docs_index import DocsIndex

_TPL_DIR = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_TPL_DIR, "page.html"), "r") as _f:
    _RESULTS_TPL = _f.read()


def _to_fts_query(query):
    """Convert user input to FTS5 prefix query: 'form val' -> '"form" * "val" *'"""
    clean = re.sub(r"[^\w\s]", " ", query).strip()
    if not clean:
        return ""
    terms = clean.split()
    return " ".join(f'"{t}"*' for t in terms)


def render(request: Request):
    query = request.args.get("q", "")

    if not query or not query.strip():
        return '''<div class="search-empty">
            <svg class="search-empty-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
            <p>Type to search across all documentation</p>
        </div>'''

    fts_query = _to_fts_query(query)
    if not fts_query:
        safe_q = escape(query)
        return f'<div class="search-no-results">No results for "<strong>{safe_q}</strong>"</div>'

    results = DocsIndex.search(fts_query)

    if not results:
        safe_q = escape(query)
        return f'<div class="search-no-results">No results for "<strong>{safe_q}</strong>"</div>'

    return render_template_string(_RESULTS_TPL, {"results": results, "query": query})
