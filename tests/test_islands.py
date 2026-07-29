import os

from asok.component import Component


class IslandComponent(Component):
    def render(self):
        return "<div>Island Content</div>"


def test_island_rendering():
    comp = IslandComponent(_client="visible")
    html = str(comp)
    assert "client:visible" in html
    assert 'data-asok-client="visible"' in html
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
    assert 'data-asok-client="idle"' in str(restored)


def test_all_hydration_strategies_kwargs():
    # 1. client="load"
    c1 = IslandComponent(client="load")
    assert "client:load" in str(c1)
    assert 'data-asok-client="load"' in str(c1)

    # 2. client_idle=True
    c2 = IslandComponent(client_idle=True)
    assert "client:idle" in str(c2)
    assert 'data-asok-client="idle"' in str(c2)

    # 3. client_only=True
    c3 = IslandComponent(client_only=True)
    assert "client:only" in str(c3)
    assert 'data-asok-client="only"' in str(c3)

    # 4. client_none=True
    c4 = IslandComponent(client_none=True)
    assert "client:none" in str(c4)
    assert 'data-asok-client="none"' in str(c4)

    # 5. client_media="(max-width: 768px)"
    c5 = IslandComponent(client_media="(max-width: 768px)")
    assert 'client:media="(max-width: 768px)"' in str(c5)
    assert 'data-asok-client="media:(max-width: 768px)"' in str(c5)
