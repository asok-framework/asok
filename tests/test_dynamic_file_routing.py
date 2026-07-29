from asok.core import Asok


def test_dynamic_file_route_resolution(tmp_path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    posts_dir = pages_dir / "posts"
    posts_dir.mkdir()

    # Create a dynamic file [id:int].py
    dynamic_file = posts_dir / "[id:int].py"
    dynamic_file.write_text("def get(req, id):\n    return f'Post {id}'")

    app = Asok(root_dir=str(tmp_path))
    app.dirs["PAGES"] = "pages"

    route_file, params = app._resolve_route(["posts", "42"])
    assert route_file == str(dynamic_file)
    assert params == {"id": 42}


def test_dynamic_file_route_string_param(tmp_path):
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    users_dir = pages_dir / "users"
    users_dir.mkdir()

    # Create a dynamic file [username].py
    dynamic_file = users_dir / "[username].py"
    dynamic_file.write_text("def get(req, username):\n    return f'User {username}'")

    app = Asok(root_dir=str(tmp_path))
    app.dirs["PAGES"] = "pages"

    route_file, params = app._resolve_route(["users", "alice"])
    assert route_file == str(dynamic_file)
    assert params == {"username": "alice"}
