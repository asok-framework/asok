from asok import Request


def render(request: Request):
    request.meta.title = "Asok : Ultra-Lightweight & Reactive Python Framework"
    return request.html("page.html")