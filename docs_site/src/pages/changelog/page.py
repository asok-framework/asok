import os
import markdown
from asok import Request


def render(request: Request):
    root_dir = request.environ.get("asok.root", os.getcwd())
    changelog_path = os.path.abspath(os.path.join(root_dir, "..", "CHANGELOG.md"))
    
    if not os.path.exists(changelog_path):
        return "<h1>Changelog file not found</h1>"

    with open(changelog_path, "r", encoding="utf-8") as f:
        md_text = f.read()
    
    html_content = markdown.markdown(
        md_text,
        extensions=['fenced_code', 'codehilite', 'tables']
    )

    return request.html("page.html", content=html_content)
