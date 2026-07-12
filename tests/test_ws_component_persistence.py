import json
import os

from asok.component import Component, exposed
from asok.ws import Connection, WebSocketServer
from asok.ws.live import on_live_message


class PersistCounter(Component):
    def mount(self, count=0):
        self.count = count

    @exposed
    def increment(self):
        self.count += 1

    def render(self):
        return f"<div>{self.count}</div>"


class MockWebsocket:
    def __init__(self):
        self.sent_messages = []
        self.closed = False

    def sendall(self, data: bytes):
        self.sent_messages.append(data)

    def close(self):
        self.closed = True


def get_sent_json(sock: MockWebsocket) -> dict:
    if not sock.sent_messages:
        return {}
    frame = sock.sent_messages[-1]
    # Simple extraction of text frame payload
    b1 = frame[1]
    length = b1 & 0x7F
    if length < 126:
        payload = frame[2:]
    elif length == 126:
        payload = frame[4:]
    else:
        payload = frame[10:]
    return json.loads(payload.decode("utf-8"))


def test_ws_join_restores_state_from_session(tmp_path):
    """Test that joining a live component restores its state from the session if present."""

    # 1. Setup mock app & session store
    class MockSessionStore:
        def __init__(self):
            self.sessions = {}

        def load(self, sid):
            return self.sessions.get(sid)

        def save(self, sid, data):
            self.sessions[sid] = dict(data)

        def generate_sid(self):
            return "session-sid-1234"

    class MockApp:
        def __init__(self):
            self._session_store = MockSessionStore()
            self.config = {"SECRET_KEY": "a" * 32}
            self.secret_key = "a" * 32

        def _precompile_directives(self, html):
            return html, {}

    app = MockApp()
    server = WebSocketServer(app)
    server.secret_key = app.secret_key
    os.environ["SECRET_KEY"] = app.secret_key

    # 2. Prepare mock session with persisted state
    comp = PersistCounter(count=42)
    signed_state = comp._sign_state(app.secret_key)
    session_data = {"_comp_my-counter-cid": signed_state}
    app._session_store.save("session-sid-1234", session_data)

    # 3. Setup mock connection and user session
    from asok.session import Session

    session = Session(session_data)
    session.sid = "session-sid-1234"

    sock = MockWebsocket()
    conn = Connection(
        sock, ("127.0.0.1", 1000), "/asok/live", {}, session=session, server=server
    )

    # 4. Trigger "join" with initial state (e.g. 0)
    initial_comp = PersistCounter(count=0)
    initial_signed = initial_comp._sign_state(app.secret_key)

    on_live_message(
        server,
        conn,
        json.dumps(
            {
                "op": "join",
                "cid": "my-counter-cid",
                "name": "PersistCounter",
                "state": initial_signed,
            }
        ),
    )

    # 5. Verify that the server restored the state (42) and sent a render update
    msg = get_sent_json(sock)
    assert msg.get("op") == "render"
    assert msg.get("cid") == "my-counter-cid"
    assert "42" in msg.get("html")
