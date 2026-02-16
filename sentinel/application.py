from fastapi import FastAPI

from sentinel.api.health_controller import router as health_router
from sentinel.api.webhook_controller import router as webhook_router

app = FastAPI(title="The Sentinel MVP")


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "The Sentinel MVP"}


app.include_router(health_router)
app.include_router(webhook_router)
