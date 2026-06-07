"""
Tests for Advanced WebSockets features.
Covers:
- Presence Tracking (online/offline transitions, reference counts, get_presence operation).
- Room Join Authorization (custom decorator, validation hooks, error payloads).
- Typing Indicators (op == "typing", routing).
- Read Receipts (op == "receipt", routing).
"""

import json
from typing import Optional

import pytest

from asok.ws import Connection, WebSocketServer
from asok.ws.live import on_live_message


class MockUser:
    def __init__(self, id: int):
        self.id = id


class MockWebsocket:
    def __init__(self):
        self.sent_messages = []
        self.closed = False

    def sendall(self, data: bytes):
        self.sent_messages.append(data)

    def shutdown(self, how: int):
        self.closed = True

    def close(self):
        self.closed = True


def get_sent_json(sock: MockWebsocket) -> Optional[dict]:
    if not sock.sent_messages:
        return None
    frame = sock.sent_messages[-1]
    if len(frame) < 2:
        return None
    b1 = frame[1]
    length = b1 & 0x7F
    if length < 126:
        payload = frame[2:]
    elif length == 126:
        payload = frame[4:]
    else:
        payload = frame[10:]
    return json.loads(payload.decode("utf-8"))


class TestWSAdvanced:
    @pytest.fixture
    def server(self):
        class MockApp:
            pass

        return WebSocketServer(MockApp())

    def test_presence_tracking_transitions_and_reference_counts(self, server):
        # 1. Setup a connection to listen for presence broadcasts
        listener_sock = MockWebsocket()
        listener_conn = Connection(
            listener_sock, ("127.0.0.1", 9999), "/asok/live", {}, server=server
        )
        server._register(listener_conn)

        user1 = MockUser(id=10)

        # First connection for user1 (transition 0 -> 1)
        conn1_sock = MockWebsocket()
        conn1 = Connection(
            conn1_sock, ("127.0.0.1", 1001), "/asok/live", {}, user=user1, server=server
        )
        server._register(conn1)

        assert server.get_online_users() == [10]
        assert server.is_user_online(10) is True

        # Check that listener received "online" broadcast
        msg = get_sent_json(listener_sock)
        assert msg == {
            "op": "broadcast",
            "type": "presence",
            "user_id": 10,
            "status": "online",
        }

        # Clear sent messages for subsequent check
        listener_sock.sent_messages.clear()

        # Second connection for user1 (transition 1 -> 2)
        conn2_sock = MockWebsocket()
        conn2 = Connection(
            conn2_sock, ("127.0.0.1", 1002), "/asok/live", {}, user=user1, server=server
        )
        server._register(conn2)

        # No new broadcast because user is already online
        assert len(listener_sock.sent_messages) == 0
        assert server.get_online_users() == [10]

        # Disconnect first tab (transition 2 -> 1)
        server._remove(conn1)
        # Should still be online, no offline broadcast
        assert len(listener_sock.sent_messages) == 0
        assert server.is_user_online(10) is True

        # Disconnect second tab (transition 1 -> 0)
        server._remove(conn2)
        assert server.get_online_users() == []
        assert server.is_user_online(10) is False

        # Check that listener received "offline" broadcast
        msg = get_sent_json(listener_sock)
        assert msg == {
            "op": "broadcast",
            "type": "presence",
            "user_id": 10,
            "status": "offline",
        }

    def test_get_presence_operation(self, server):
        user1 = MockUser(id=101)
        conn1 = Connection(
            MockWebsocket(),
            ("127.0.0.1", 1001),
            "/asok/live",
            {},
            user=user1,
            server=server,
        )
        server._register(conn1)

        user2 = MockUser(id=102)
        conn2 = Connection(
            MockWebsocket(),
            ("127.0.0.1", 1002),
            "/asok/live",
            {},
            user=user2,
            server=server,
        )
        server._register(conn2)

        sock = MockWebsocket()
        conn_query = Connection(
            sock, ("127.0.0.1", 9999), "/asok/live", {}, server=server
        )

        # Trigger get_presence query
        on_live_message(server, conn_query, json.dumps({"op": "get_presence"}))

        msg = get_sent_json(sock)
        assert msg is not None
        assert msg["op"] == "broadcast"
        assert msg["type"] == "presence_list"
        assert set(msg["users"]) == {101, 102}

    def test_room_join_authorization_hook(self, server):
        @server.room_authorizer
        def my_auth(conn, room):
            if room == "admin-only":
                return conn.user and getattr(conn.user, "is_admin", False)
            return True

        user_admin = MockUser(id=1)
        user_admin.is_admin = True

        user_regular = MockUser(id=2)
        user_regular.is_admin = False

        # Regular user fails to join admin room
        sock_reg = MockWebsocket()
        conn_reg = Connection(
            sock_reg,
            ("127.0.0.1", 1001),
            "/asok/live",
            {},
            user=user_regular,
            server=server,
        )
        on_live_message(
            server, conn_reg, json.dumps({"op": "join_room", "room": "admin-only"})
        )

        # Check that error is sent back
        msg_err = get_sent_json(sock_reg)
        assert msg_err is not None
        assert msg_err["op"] == "broadcast"
        assert msg_err["type"] == "error"
        assert msg_err["room"] == "admin-only"
        assert "Unauthorized" in msg_err["message"]

        # Admin user joins successfully
        sock_admin = MockWebsocket()
        conn_admin = Connection(
            sock_admin,
            ("127.0.0.1", 1002),
            "/asok/live",
            {},
            user=user_admin,
            server=server,
        )
        on_live_message(
            server, conn_admin, json.dumps({"op": "join_room", "room": "admin-only"})
        )

        # No error messages sent back
        assert len(sock_admin.sent_messages) == 0
        assert "admin-only" in conn_admin._rooms

    def test_typing_indicators(self, server):
        user1 = MockUser(id=201)
        sock1 = MockWebsocket()
        conn1 = Connection(
            sock1, ("127.0.0.1", 1001), "/asok/live", {}, user=user1, server=server
        )
        server._register(conn1)
        conn1._rooms.add("chat-room")

        user2 = MockUser(id=202)
        sock2 = MockWebsocket()
        conn2 = Connection(
            sock2, ("127.0.0.1", 1002), "/asok/live", {}, user=user2, server=server
        )
        server._register(conn2)
        conn2._rooms.add("chat-room")

        # Clear presence broadcast messages before testing typing
        sock1.sent_messages.clear()
        sock2.sent_messages.clear()

        # user1 starts typing
        on_live_message(
            server,
            conn1,
            json.dumps({"op": "typing", "room": "chat-room", "typing": True}),
        )

        # user2 should receive the typing indicator
        msg2 = get_sent_json(sock2)
        assert msg2 is not None
        assert msg2["op"] == "broadcast"
        assert msg2["type"] == "typing"
        assert msg2["room"] == "chat-room"
        assert msg2["user_id"] == 201
        assert msg2["typing"] is True

        # user1 should NOT receive their own typing indicator
        assert len(sock1.sent_messages) == 0

    def test_read_receipts(self, server):
        user1 = MockUser(id=201)
        sock1 = MockWebsocket()
        conn1 = Connection(
            sock1, ("127.0.0.1", 1001), "/asok/live", {}, user=user1, server=server
        )
        server._register(conn1)
        conn1._rooms.add("chat-room")

        user2 = MockUser(id=202)
        sock2 = MockWebsocket()
        conn2 = Connection(
            sock2, ("127.0.0.1", 1002), "/asok/live", {}, user=user2, server=server
        )
        server._register(conn2)
        conn2._rooms.add("chat-room")

        # Clear presence broadcast messages before testing read receipts
        sock1.sent_messages.clear()
        sock2.sent_messages.clear()

        # user1 sends read receipt
        on_live_message(
            server,
            conn1,
            json.dumps(
                {
                    "op": "receipt",
                    "room": "chat-room",
                    "message_id": 555,
                    "status": "read",
                }
            ),
        )

        # user2 should receive the read receipt
        msg2 = get_sent_json(sock2)
        assert msg2 is not None
        assert msg2["op"] == "broadcast"
        assert msg2["type"] == "receipt"
        assert msg2["room"] == "chat-room"
        assert msg2["message_id"] == 555
        assert msg2["user_id"] == 201
        assert msg2["status"] == "read"

        # user1 should NOT receive their own read receipt
        assert len(sock1.sent_messages) == 0
