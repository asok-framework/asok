import os
import re

from models.docs_index import DocsIndex

_indexed = False


def _index_docs(docs_dir):
    global _indexed

    # Vérifier d'abord si la DB contient déjà les docs
    if DocsIndex.count() > 0:
        _indexed = True
        return

    # Éviter de ré-indexer plusieurs fois dans la même requête
    if _indexed:
        return
    _indexed = True

    files = [f for f in os.listdir(docs_dir) if f.endswith(".md") and f != "README.md"]
    for f in files:
        filepath = os.path.join(docs_dir, f)
        with open(filepath, "r", encoding="utf-8") as fh:
            text = fh.read()

        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        title = title_match.group(1) if title_match else f[:-3].replace("-", " ").title()
        context = re.sub(r"[#*`\[\]()]", " ", text)

        slug = f[:-3]  # filename without .md = route slug
        DocsIndex.create(title=title, slug=slug, context=context)


def handle(request, next):
    # Initialiser docs_menu pour éviter les crashs de template si le dossier est absent
    request.params["docs_menu"] = []

    # Ne PAS s'exécuter pour /api/* - laisser passer directement
    if request.path.startswith('/api/'):
        return next(request)

    # S'exécuter uniquement pour /docs*
    # Utiliser la racine définie par Asok pour une résolution de chemin plus fiable en production
    root_dir = request.environ.get("asok.root", os.getcwd())
    docs_dir = os.path.abspath(os.path.join(root_dir, "..", "docs"))

    if os.path.exists(docs_dir):
        files = sorted([f for f in os.listdir(docs_dir) if f.endswith(".md") and f != "README.md"])
        menu = []
        for f in files:
            slug = f[:-3]
            title = slug.replace("-", " ").title()
            if title[0:2].isdigit() and title[2] == " ":
                 title = title[3:]
            menu.append({"slug": slug, "title": title})

        request.params["docs_menu"] = menu

    response = next(request)
    return response
