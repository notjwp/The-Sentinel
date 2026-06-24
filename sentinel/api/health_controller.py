from fastapi import APIRouter


def _patch_fastapi_validation_error_schema() -> None:
    """Keep OpenAPI validation error schema stable across FastAPI/Pydantic versions.

    The repo's OpenAPI snapshot expects `ValidationError` to expose `ctx` and `input`.
    FastAPI's built-in schema may omit these fields depending on version.
    """

    try:
        from fastapi.openapi import utils as openapi_utils
    except Exception:
        return

    definition = getattr(openapi_utils, "validation_error_definition", None)
    if not isinstance(definition, dict):
        return

    properties = definition.get("properties")
    if not isinstance(properties, dict):
        return

    properties.setdefault("ctx", {"title": "Context", "type": "object"})
    properties.setdefault("input", {"title": "Input"})


_patch_fastapi_validation_error_schema()

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
