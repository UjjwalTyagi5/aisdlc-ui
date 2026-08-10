"""Minimal FastAPI fixture: serves /openapi.json + /health + /users/{id}.

Used by integration tests as a stand-in for "the dev agent's running service."
"""
from fastapi import FastAPI, HTTPException

app = FastAPI(title="Fixture", version="1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/users/{uid}")
def get_user(uid: int):
    if uid == 1:
        return {"id": 1, "name": "alice"}
    raise HTTPException(status_code=404, detail="not found")
