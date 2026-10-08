"""HTTP-level authentication rotation acceptance flow."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.e2e


def test_register_login_refresh_logout_and_reuse_detection() -> None:
    email = f"e2e-{uuid.uuid4()}@example.com"
    with TestClient(create_app(), base_url="http://localhost") as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "email": email,
                "password": "StrongPassword123",
                "first_name": "E2E",
                "last_name": "User",
            },
        )
        assert registered.status_code == 201, registered.text
        access_token = registered.json()["data"]["tokens"]["access_token"]
        profile_update = client.put(
            "/api/v1/users/me",
            json={"first_name": "Updated", "last_name": "User"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        assert profile_update.status_code == 200, profile_update.text
        first_refresh = registered.json()["data"]["tokens"]["refresh_token"]

        logged_in = client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": "StrongPassword123"},
        )
        assert logged_in.status_code == 200, logged_in.text

        rotated = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
        )
        assert rotated.status_code == 200, rotated.text
        second_refresh = rotated.json()["data"]["refresh_token"]

        reused = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
        )
        assert reused.status_code == 401
        assert reused.json()["error"]["code"] == "REFRESH_TOKEN_REUSE"

        logged_out = client.post(
            "/api/v1/auth/logout", json={"refresh_token": second_refresh}
        )
        assert logged_out.status_code == 200
