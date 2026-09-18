from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    claude_model: str = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
    image_backend: str = os.environ.get("IMAGE_BACKEND", "mock")
    stability_api_key: str = os.environ.get("STABILITY_API_KEY", "")
    data_dir: str = os.environ.get("DATA_DIR", "data/projects")


settings = Settings()
