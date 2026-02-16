from typing import Any

from fastapi import APIRouter, Body

router = APIRouter()


@router.post("/webhook")
def webhook(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"received": payload}
