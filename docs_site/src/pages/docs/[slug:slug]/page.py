import os
import re
import markdown
from asok import Request

# Navigation markdown destinée à GitHub (← Previous | Docs | Next →)
_NAV_RE = re.compile(
    r'^(?:\[[^\]]*\]\([^)]*\.md\)\s*\|\s*){1,2}\[[^\]]*\]\([^)]*\.md\)$',
    re.MULTILINE
)

def render(request: Request):
    # On remonte de docs/[slug:slug]/page.py -> docs/ -> src/ -> docs_site/ -> racine/docs
    slug = request.params.get("slug")
    # On cherche le dossier docs à la racine du projet Asok
    root_dir = request.environ.get("asok.root", os.getcwd())
    docs_dir = os.path.abspath(os.path.join(root_dir, "..", "docs"))
    filepath = os.path.join(docs_dir, f"{slug}.md")

    
    if not os.path.exists(filepath):
        request.status_code(404)
        return "<h1>404 Not Found</h1>"

    with open(filepath, "r", encoding="utf-8") as f:
        md_text = f.read()
    

    # Retirer la navigation markdown (utile sur GitHub, redondante ici)
    md_text = _NAV_RE.sub('', md_text).rstrip()

    # Retirer la barre horizontale finale si elle précédait la navigation
    md_text = re.sub(r'\n---\s*$', '', md_text)

    # Conversion Markdown -> HTML avec Pygments pour le code
    html_content = markdown.markdown(
        md_text,
        extensions=['fenced_code', 'codehilite', 'tables']
    )
    
    # Next / Prev navigation
    menu = request.params.get("docs_menu", [])
    idx = -1
    if slug:
        for i, item in enumerate(menu):
            if item["slug"] == slug:
                idx = i
                break
    
    prev_page = menu[idx-1] if idx > 0 else None
    next_page = menu[idx+1] if idx < len(menu)-1 and idx != -1 else None

    return request.html("page.html",
        content=html_content,
        slug=slug,
        title=slug.replace("-", " ").title(),
        prev_page=prev_page,
        next_page=next_page
    )
