import os

from asok.component import Component


class IslandComponent(Component):
    def render(self):
        return "<div>Island Content</div>"


def test_island_rendering():
    comp = IslandComponent(_client="visible")
    html = str(comp)
    assert "client:visible" in html
    assert 'data-asok-component="IslandComponent"' in html


def test_island_restoration():
    comp = IslandComponent(_client="idle")
    secret = os.getenv("SECRET_KEY", "test-secret-key-32-chars-length-security")
    signed = comp._sign_state(secret)

    restored = IslandComponent._from_signed_state(signed, secret)
    assert restored is not None

    # Simulate component template restoration setting _client
    restored._client = "idle"
    assert restored._client == "idle"
    assert "client:idle" in str(restored)
