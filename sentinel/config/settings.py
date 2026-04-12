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
    ENABLE_GITHUB: bool
    ENABLE_TRANSLATION: bool
    ENABLE_DOC_REVIEW: bool
    GITHUB_APP_ID: str | None
    GITHUB_INSTALLATION_ID: str | None
    GITHUB_PRIVATE_KEY: str | None
    GITHUB_API_BASE_URL: str


def get_settings() -> Settings:
    raw_key = os.getenv("NVIDIA_API_KEY")
    nvidia_key = raw_key.strip() if raw_key is not None else None
    if nvidia_key == "":
        nvidia_key = None

    raw_private_key = os.getenv("GITHUB_PRIVATE_KEY")
    github_private_key = raw_private_key.strip() if raw_private_key is not None else None
    if github_private_key == "":
        github_private_key = None
    elif github_private_key is not None:
        github_private_key = github_private_key.replace("\\n", "\n")

    github_app_id = os.getenv("GITHUB_APP_ID")
    if github_app_id is not None:
        github_app_id = github_app_id.strip() or None

    github_installation_id = os.getenv("GITHUB_INSTALLATION_ID")
    if github_installation_id is not None:
        github_installation_id = github_installation_id.strip() or None

    github_api_base_url = os.getenv("GITHUB_API_BASE_URL")
    if github_api_base_url is None:
        github_api_base_url = "https://api.github.com"
    github_api_base_url = github_api_base_url.strip() or "https://api.github.com"

    return Settings(
        NVIDIA_API_KEY=nvidia_key,
        ENABLE_LLM=_to_bool(os.getenv("ENABLE_LLM"), True),
        LLM_MAX_CALLS=_to_int(os.getenv("LLM_MAX_CALLS"), 5),
        LLM_TIMEOUT=_to_float(os.getenv("LLM_TIMEOUT"), 10.0),
        ENABLE_GITHUB=_to_bool(os.getenv("ENABLE_GITHUB"), True),
        ENABLE_TRANSLATION=_to_bool(os.getenv("ENABLE_TRANSLATION"), True),
        ENABLE_DOC_REVIEW=_to_bool(os.getenv("ENABLE_DOC_REVIEW"), True),
        GITHUB_APP_ID=github_app_id,
        GITHUB_INSTALLATION_ID=github_installation_id,
        GITHUB_PRIVATE_KEY=github_private_key,
        GITHUB_API_BASE_URL=github_api_base_url,
    )
