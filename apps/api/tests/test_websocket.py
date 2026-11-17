from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import create_app
from tests.conftest import ADMIN_EMAIL, STRONG_PASSWORD


@pytest.mark.usefixtures("release_global_clients")
def test_websocket_auth_status_and_ping() -> None:
    with TestClient(create_app()) as tc:
        # Unauthenticated connection is refused.
        with pytest.raises(WebSocketDisconnect), tc.websocket_connect("/api/ws") as ws:
            ws.receive_json()

        r = tc.post("/api/auth/bootstrap", json={"email": ADMIN_EMAIL, "password": STRONG_PASSWORD})
        assert r.status_code == 201

        # Disallowed browser origin is refused even with a valid session.
        with (
            pytest.raises(WebSocketDisconnect),
            tc.websocket_connect("/api/ws", headers={"origin": "https://evil.example"}) as ws,
        ):
            ws.receive_json()

        with tc.websocket_connect("/api/ws", headers={"origin": "http://localhost:3000"}) as ws:
            first = ws.receive_json()
            assert first["topic"] == "system.status"
            assert first["data"]["execution_mode"] == "ANALYSIS_ONLY"
            ws.send_json({"type": "ping", "id": 7})
            pong = ws.receive_json()
            assert pong["topic"] == "pong" and pong["data"] == {"echo": 7}
            assert pong["seq"] > first["seq"]
