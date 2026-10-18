"""Auth Service: issues and validates JWTs for the dashboard."""
import hmac
import os
import time

import jwt
from fastapi import FastAPI, Header, HTTPException
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
TOKEN_TTL = int(os.getenv("TOKEN_TTL_SECONDS", "28800"))  # 8 hours

app = FastAPI(title="Auth Service")
Instrumentator().instrument(app).expose(app)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/auth/login")
def login(body: LoginRequest):
    user_ok = hmac.compare_digest(body.username, ADMIN_USER)
    pass_ok = hmac.compare_digest(body.password, ADMIN_PASSWORD)
    if not (user_ok and pass_ok):
        raise HTTPException(status_code=401, detail="Wrong username or password")
    now = int(time.time())
    claims = {"sub": body.username, "iat": now, "exp": now + TOKEN_TTL}
    token = jwt.encode(claims, JWT_SECRET, algorithm="HS256")
    return {"token": token, "username": body.username, "expires_in": TOKEN_TTL}


# The API Gateway calls this for every protected request (nginx auth_request).
@app.api_route("/api/auth/verify", methods=["GET", "POST"])
def verify(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return {"valid": True, "user": claims["sub"]}
