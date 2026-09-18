from __future__ import annotations

import uuid
from pathlib import Path

from app.config import settings
from app.models import ComicProject


def new_project_id() -> str:
    return uuid.uuid4().hex[:12]


def project_dir(project_id: str) -> Path:
    return Path(settings.data_dir) / project_id


def save_project(project: ComicProject) -> None:
    directory = project_dir(project.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "project.json").write_text(project.model_dump_json(indent=2))


def load_project(project_id: str) -> ComicProject:
    path = project_dir(project_id) / "project.json"
    if not path.exists():
        raise FileNotFoundError(f"No project with id {project_id}")
    return ComicProject.model_validate_json(path.read_text())
