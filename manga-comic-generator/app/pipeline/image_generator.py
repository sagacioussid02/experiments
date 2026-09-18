from __future__ import annotations

import textwrap
from abc import ABC, abstractmethod
from pathlib import Path

import requests
from PIL import Image, ImageDraw

from app.config import settings
from app.models import CharacterProfile, Panel


def build_panel_prompt(panel: Panel, characters: list[CharacterProfile]) -> str:
    """Builds the text prompt sent to whichever ImageGenerator backend is active.

    Re-stating each present character's fixed visual_description alongside the scene is the
    cheapest available consistency trick for plain text-to-image: it can't guarantee identical
    faces across panels the way image-conditioning (IP-Adapter/InstantID) can, but it keeps
    hair color/outfit/species etc. from drifting panel to panel, at zero extra cost.
    """
    by_name = {c.name: c for c in characters}
    sheet = "\n".join(
        f"{name} looks like: {by_name[name].visual_description}"
        for name in panel.characters
        if name in by_name and by_name[name].visual_description
    )
    parts = [f"Manga panel, {panel.camera_angle}. {panel.scene_description}"]
    if sheet:
        parts.append(sheet)
    parts.append("Black and white manga ink style, clean linework, dynamic composition.")
    return "\n".join(parts)


class ImageGenerator(ABC):
    @abstractmethod
    def generate_panel(self, panel: Panel, characters: list[CharacterProfile], output_path: Path) -> Path:
        """Render one panel to output_path and return it."""


class MockImageGenerator(ImageGenerator):
    """Zero-dependency, zero-cost stand-in: draws the prompt text onto a placeholder canvas.

    Exists so the rest of the pipeline (story -> script -> layout -> PDF) can be built, run,
    and tested without an image-gen API key or network access. See DESIGN.md, Stage 4.
    """

    CANVAS_SIZE = (768, 768)

    def generate_panel(self, panel: Panel, characters: list[CharacterProfile], output_path: Path) -> Path:
        prompt = build_panel_prompt(panel, characters)
        image = Image.new("RGB", self.CANVAS_SIZE, color=(235, 235, 235))
        draw = ImageDraw.Draw(image)
        draw.rectangle(
            [4, 4, self.CANVAS_SIZE[0] - 4, self.CANVAS_SIZE[1] - 4], outline=(0, 0, 0), width=4
        )
        wrapped = "\n".join(textwrap.wrap(prompt, width=48))
        draw.multiline_text((24, 24), wrapped, fill=(20, 20, 20))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return output_path


class StabilityAIImageGenerator(ImageGenerator):
    """Real backend: Stability AI's text-to-image REST API.

    Swapping to a different vendor (Gemini/Imagen, OpenAI images) or a self-hosted Stable
    Diffusion + IP-Adapter/InstantID server later means writing one more ImageGenerator
    subclass -- nothing in story_generator/script_generator/layout needs to change.

    TODO (character consistency, next step): pass each panel's first character's
    reference_image_path as an image-to-image conditioning input instead of relying on the
    text prompt alone -- see DESIGN.md, Stage 4.
    """

    API_URL = "https://api.stability.ai/v2beta/stable-image/generate/core"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.stability_api_key
        if not self.api_key:
            raise RuntimeError("STABILITY_API_KEY is not set")

    def generate_panel(self, panel: Panel, characters: list[CharacterProfile], output_path: Path) -> Path:
        prompt = build_panel_prompt(panel, characters)
        response = requests.post(
            self.API_URL,
            headers={"authorization": f"Bearer {self.api_key}", "accept": "image/*"},
            files={"none": ""},
            data={"prompt": prompt, "output_format": "png"},
            timeout=60,
        )
        response.raise_for_status()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)
        return output_path


def get_image_generator(backend: str | None = None) -> ImageGenerator:
    backend = backend or settings.image_backend
    if backend == "mock":
        return MockImageGenerator()
    if backend == "stability":
        return StabilityAIImageGenerator()
    raise ValueError(f"Unknown image backend: {backend}")
