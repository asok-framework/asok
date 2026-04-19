"""
Tests for the WebSocket server and connection wrapper.
Covers: Connection object (send/close), WebSocketServer (registration, broadcasting, event handlers).
"""

import pytest

from asok.ws import Connection, WebSocketServer

# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockWebsocket:
    def __init__(self):
        self.sent_messages = []
        self.closed = False

    def sendall(self, data):
        self.sent_messages.append(data)

    def shutdown(self, how):
        self.closed = True

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Connection Wrapper
# ---------------------------------------------------------------------------


class TestConnection:
    def test_connection_initialization(self):
        mock_ws = MockWebsocket()
        env = {"HTTP_X_REAL_IP": "192.168.1.1"}
        conn = Connection(mock_ws, ("127.0.0.1", 1234), "/", env)

        assert conn.path == "/"
        assert conn.headers == env
        assert conn.session is None
        assert conn.user is None

    def test_connection_send(self):
        mock_ws = MockWebsocket()
        conn = Connection(mock_ws, ("127.0.0.1", 1234), "/", {})
        conn.send("Hello WS")
        assert b"Hello WS" in mock_ws.sent_messages[0]

    def test_connection_send_json(self):

        mock_ws = MockWebsocket()
        conn = Connection(mock_ws, ("127.0.0.1", 1234), "/", {})
        conn.send_json({"event": "ping"})

        # Verify JSON formatting is inside the frame (very roughly, as it's binary data)
        sent_data = mock_ws.sent_messages[0]
        assert b"ping" in sent_data

    def test_connection_close(self):
        mock_ws = MockWebsocket()
        conn = Connection(mock_ws, ("127.0.0.1", 1234), "/", {})
        conn.close()
        assert mock_ws.closed


# ---------------------------------------------------------------------------
# WebSocket Server
# ---------------------------------------------------------------------------


class TestWebSocketServer:
    @pytest.fixture
    def server(self):
        # Pass a mock app (just an empty object)
        class MockApp:
            pass

        return WebSocketServer(MockApp())

    def test_server_registers_event_handlers(self, server):
        @server.on("/chat")
        def my_handler(conn, data):
            pass

        route = server._route("/chat")
        assert route.on_message == my_handler

    def test_server_registers_lifecycle_hooks(self, server):
        @server.on_connect("/chat")
        def on_conn(conn):
            pass

        @server.on_disconnect("/chat")
        def on_disconn(conn):
            pass

        route = server._route("/chat")
        assert route.on_connect == on_conn
        assert route.on_disconnect == on_disconn

    def test_server_tracks_connections(self, server):
        conn = Connection(MockWebsocket(), ("127.0.0.1", 1234), "/", {})

        # Simulate connection added
        server._connections["/"] = {conn}
        assert conn in server.connections("/")

    def test_server_broadcast(self, server):
        c1 = Connection(MockWebsocket(), ("127.0.0.1", 1234), "/", {})
        c2 = Connection(MockWebsocket(), ("127.0.0.1", 1235), "/", {})
        server._connections["/"] = {c1, c2}

        server.broadcast("/", "Alert")

        assert b"Alert" in c1.sock.sent_messages[0]
        assert b"Alert" in c2.sock.sent_messages[0]

    def test_server_broadcast_to_specific_ids(self, server):
        c1 = Connection(MockWebsocket(), ("127.0.0.1", 1234), "/", {})
        c2 = Connection(MockWebsocket(), ("127.0.0.1", 1235), "/", {})
        c3 = Connection(MockWebsocket(), ("127.0.0.1", 1236), "/", {})
        server._connections["/"] = {c1, c2, c3}

        c1._rooms.add("my_room")
        c3._rooms.add("my_room")

        server.broadcast_to("my_room", "Targeted")

        assert b"Targeted" in c1.sock.sent_messages[0]
        assert b"Targeted" in c3.sock.sent_messages[0]
        assert len(c2.sock.sent_messages) == 0
