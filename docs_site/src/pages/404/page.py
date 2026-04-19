from asok import Request


def render(request: Request):
    request.status_code(404)
    return request.html("page.html")