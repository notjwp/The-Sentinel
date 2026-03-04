import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.health_controller import router as health_router
from sentinel.api.webhook_controller import get_orchestrator
from sentinel.api.webhook_controller import router as webhook_router

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent / "snapshots"
SNAPSHOT_FILE = SNAPSHOT_DIR / "openapi_snapshot.json"


class _NoopOrchestrator:
    async def enqueue_pull_request(self, payload: dict) -> None:
        pass


def _build_app() -> FastAPI:
    app = FastAPI(title="The Sentinel")
    app.dependency_overrides[get_orchestrator] = lambda: _NoopOrchestrator()
    app.include_router(webhook_router)
    app.include_router(health_router)

    @app.get("/")
    def root() -> dict:
        return {"message": "The Sentinel is running"}

    return app


def _get_openapi_schema() -> dict:
    client = TestClient(_build_app())
    response = client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _save_snapshot(schema: dict) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_FILE.write_text(
        json.dumps(schema, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _load_snapshot() -> dict | None:
    if not SNAPSHOT_FILE.exists():
        return None
    return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def test_openapi_schema_snapshot_matches():
    current = _get_openapi_schema()
    existing = _load_snapshot()

    if existing is None:
        _save_snapshot(current)
        existing = current

    assert current == existing, (
        "OpenAPI schema has changed. If this change is intentional, "
        "delete sentinel/tests/snapshots/openapi_snapshot.json and re-run."
    )


def test_openapi_schema_has_webhook_endpoint():
    schema = _get_openapi_schema()
    paths = schema.get("paths", {})
    assert "/webhook" in paths
    assert "post" in paths["/webhook"]


def test_openapi_schema_has_health_endpoint():
    schema = _get_openapi_schema()
    paths = schema.get("paths", {})
    assert "/health" in paths
    assert "get" in paths["/health"]


def test_openapi_schema_has_root_endpoint():
    schema = _get_openapi_schema()
    paths = schema.get("paths", {})
    assert "/" in paths
    assert "get" in paths["/"]


def test_openapi_schema_title_is_sentinel():
    schema = _get_openapi_schema()
    assert schema["info"]["title"] == "The Sentinel"


def test_webhook_endpoint_requires_request_body():
    schema = _get_openapi_schema()
    webhook_post = schema["paths"]["/webhook"]["post"]
    assert "requestBody" in webhook_post


def test_health_endpoint_returns_200_response():
    schema = _get_openapi_schema()
    health_get = schema["paths"]["/health"]["get"]
    assert "200" in health_get["responses"]
