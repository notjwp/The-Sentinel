import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(0, parsed)


def _to_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    return max(0.0, parsed)


@dataclass(frozen=True)
class Settings:
    NVIDIA_API_KEY: str | None
    ENABLE_LLM: bool
    LLM_MAX_CALLS: int
    LLM_TIMEOUT: float


def get_settings() -> Settings:
    raw_key = os.getenv("NVIDIA_API_KEY")
    nvidia_key = raw_key.strip() if raw_key is not None else None
    if nvidia_key == "":
        nvidia_key = None

    return Settings(
        NVIDIA_API_KEY=nvidia_key,
        ENABLE_LLM=_to_bool(os.getenv("ENABLE_LLM"), True),
        LLM_MAX_CALLS=_to_int(os.getenv("LLM_MAX_CALLS"), 5),
        LLM_TIMEOUT=_to_float(os.getenv("LLM_TIMEOUT"), 10.0),
    )
