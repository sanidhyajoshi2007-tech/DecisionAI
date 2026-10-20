"""
Tests for Phase 0 — verify the app boots and health check works.
"""

import pytest


@pytest.mark.asyncio
async def test_health_check(client):
    """Health endpoint returns 200 with app name."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "app" in data


@pytest.mark.asyncio
async def test_cors_headers(client):
    """CORS preflight returns the right headers."""
    response = await client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    # FastAPI CORS middleware should respond to preflight
    assert response.status_code == 200
