from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    """Minimal .env loader. The project .env overrides the shell environment so a stale exported
    key (e.g. in ~/.bash_profile) can't silently shadow the key configured for this app."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"'")


_load_dotenv()


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_model: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
    image_backend: str = os.environ.get("IMAGE_BACKEND", "mock")
    stability_api_key: str = os.environ.get("STABILITY_API_KEY", "")
    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_image_model: str = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-1")
    openai_image_quality: str = os.environ.get("OPENAI_IMAGE_QUALITY", "medium")
    openai_input_fidelity: str = os.environ.get("OPENAI_INPUT_FIDELITY", "low")
    openai_reference_max_side: int = int(os.environ.get("OPENAI_REFERENCE_MAX_SIDE", "1024"))
    openai_sheet_fidelity: str = os.environ.get("OPENAI_SHEET_FIDELITY", "high")
    judge_model: str = os.environ.get("JUDGE_MODEL", "gpt-5")
    judge_reasoning_effort: str = os.environ.get("JUDGE_REASONING_EFFORT", "low")
    locator_model: str = os.environ.get("LOCATOR_MODEL", "gpt-5-mini")
    require_bible_approval: bool = os.environ.get("REQUIRE_BIBLE_APPROVAL", "true").lower() not in ("0", "false", "no")
    max_comic_cost_usd: float = float(os.environ.get("MAX_COMIC_COST_USD", "5.0"))
    data_dir: str = os.environ.get("DATA_DIR", "data/projects")


settings = Settings()
