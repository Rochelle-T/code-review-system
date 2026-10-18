from fastapi.testclient import TestClient

from conftest import load_service

auth = load_service("auth-service", "app.py", "auth_app")
client = TestClient(auth.app)


def test_login_and_verify():
    resp = client.post("/api/auth/login", json={"username": auth.ADMIN_USER, "password": auth.ADMIN_PASSWORD})
    assert resp.status_code == 200
    token = resp.json()["token"]
    check = client.get("/api/auth/verify", headers={"Authorization": f"Bearer {token}"})
    assert check.status_code == 200 and check.json()["valid"] is True


def test_wrong_password_rejected():
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "nope"})
    assert resp.status_code == 401


def test_verify_rejects_garbage_token():
    resp = client.get("/api/auth/verify", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
