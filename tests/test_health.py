import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.parametrize("path", ["/health", "/healthz"])
async def test_health(path: str) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get(path)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
