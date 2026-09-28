from fastapi.testclient import TestClient

from app.main import app


def test_health_ok() -> None:
    # No auth header, no DB dependency override -- /health must work regardless
    # of whether Supabase is reachable (see the comment in app/main.py).
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
