"""Landing page at ``/``."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_landing_returns_html():
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    # Spot-check that the key sections are present — cheap guard against
    # future accidental deletions.
    assert "Cogrant Search API" in body
    assert "/v1/searches" in body
    assert "Authorization: Bearer" in body
    assert "/docs" in body
    assert "/openapi.json" in body


def test_landing_is_not_in_openapi_schema():
    # The landing page is a marketing surface, not part of the contract.
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/" not in paths
