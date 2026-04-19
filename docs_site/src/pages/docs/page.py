from asok import Request

def render(request: Request):
    if not request.params.get("docs_menu"):
        return "No documentation found.", 404
        
    first_slug = request.params["docs_menu"][0]["slug"]
    return request.redirect(f"/docs/{first_slug}")
