"""Shared helpers for the judge-evaluation tools: the panel set and Bruno's judging setup."""
import json
import random
from pathlib import Path

from app.pipeline.judge import DEFAULT_CHECKS
from app.storage import load_project

WORK = Path("data/projects/_labeling")
PROJECTS = ["ece8ba5d2799", "242395bfeb3a", "212cf525356e"]  # three test runs, before/after each fix
STANDARD = "212cf525356e"  # Bruno's approved sheet + Sol's clean sheet come from here

BRUNO_CHECKS = list(DEFAULT_CHECKS)
BRUNO_CHECKS[1] = ("Nose: a large rounded-oval, matte black nose about one third of the muzzle width, at every angle "
                   "-- FAIL if it is small, triangular, pointed or shrunk to a dot")


def collect_panels() -> list[dict]:
    """All finished panels across the test runs, in a fixed shuffled order with neutral ids (P01..)."""
    panels = []
    for pid in PROJECTS:
        for page in load_project(pid).pages:
            for panel in page.panels:
                if panel.image_path and Path(panel.image_path).exists():
                    panels.append({"source": f"{pid}:{page.page_number}-{panel.panel_number}", "image": panel.image_path, "cast": panel.characters})
    random.Random(7).shuffle(panels)
    for i, p in enumerate(panels, 1):
        p["id"] = f"P{i:02d}"
    return panels


def standard_characters() -> dict:
    return {c.name: c for c in load_project(STANDARD).characters}


def load_json(name: str, default):
    path = WORK / name
    return json.loads(path.read_text()) if path.exists() else default
