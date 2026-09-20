from __future__ import annotations

import base64
import io
import textwrap
import time
from abc import ABC, abstractmethod
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageOps

from app.config import settings
from app.http import post_with_retry, raise_for_status
from app.models import CharacterProfile, Panel, StoryArc
from app.pipeline.bible import bible_prompt_block
from app.usage import openai_image_record


def build_panel_prompt(panel: Panel, characters: list[CharacterProfile]) -> str:
    """Builds the text prompt sent to whichever ImageGenerator backend is active.

    Re-stating each present character's fixed visual_description alongside the scene is the
    cheapest available consistency trick for plain text-to-image: it can't guarantee identical
    faces across panels the way image-conditioning (IP-Adapter/InstantID) can, but it keeps
    hair color/outfit/species etc. from drifting panel to panel, at zero extra cost.
    """
    by_name = {c.name: c for c in characters}
    present = [by_name[name] for name in panel.characters if name in by_name]
    sheet = "\n".join(bible_prompt_block(c) or (f"{c.name} looks like: {c.visual_description}" if c.visual_description else "") for c in present).strip()
    parts = [f"Manga panel, {panel.camera_angle}. {panel.scene_description}"]
    if len(present) > 1:
        parts.append("Characters left to right in the frame: " + ", ".join(c.name for c in present) + ".")
    if sheet:
        parts.append(sheet)
    if present:
        parts.append(
            "Draw ONLY these characters: " + ", ".join(c.name for c in present) + ". Do not add any other "
            "character, person, human, hand, animal or creature to the frame."
        )
    else:
        parts.append("Draw no characters in this panel, only the setting. Do not add any person, hand or creature.")
    if panel.retry_hint:
        parts.append("CORRECTIONS -- the previous attempt at this panel had these problems; fix them exactly: " + panel.retry_hint)
    parts.append(
        "Black and white manga ink style, clean linework, screentone shading, dynamic composition. "
        "Keep every character exactly as in their reference sheet and the descriptions above: same face, "
        "proportions, markings and texture. Do not add claws, fangs, teeth or any feature the reference "
        "does not show. Keep the top third of the frame relatively open (sky, wall, background) because "
        "speech bubbles are added there later. "
        "Do NOT draw any text at all: no letters, words, onomatopoeia or sound effects (no CREAK, THUD, "
        "BANG and the like), no speech bubbles, captions or panel borders. Show sounds with motion lines only."
    )
    return "\n".join(parts)


def build_sheet_prompt(character: CharacterProfile) -> str:
    return (
        f"Character reference sheet of {character.name} for a black-and-white manga. "
        "Layout: one large full-body front view on the left; on the right a grid of smaller views: a "
        "three-quarter view, a low-angle view looking up at the character, a seated pose, a close-up of the "
        "face with a neutral expression, and a close-up with a strong emotion (angry or shouting) drawn "
        "exactly according to the design rules. Every view must keep the identical design. "
        "Pure white background: no gradient, vignette, glow, shadow, floor or backdrop, and no frames or "
        "divider lines between the views. Evenly lit, ink linework with screentone shading. "
        "Draw ONLY the character from the reference photo -- ignore any hands, people, other "
        "characters, furniture or backdrop in it. Preserve the exact design: shape, proportions, "
        "eyes, nose, ears, markings/patches and fur or yarn texture. "
        + (bible_prompt_block(character) + "\n" if bible_prompt_block(character) else (f"Appearance notes: {character.visual_description}. " if character.visual_description else ""))
        + "Do NOT draw any text, labels, arrows or borders."
    )


def build_cover_prompt(story: StoryArc, characters: list[CharacterProfile]) -> str:
    """The title is deliberately NOT in the prompt: image models try to letter it, badly. The
    compositor adds the title on top of the art."""
    names = ", ".join(c.name for c in characters)
    looks = "\n".join(bible_prompt_block(c) or (f"{c.name} looks like: {c.visual_description}" if c.visual_description else "") for c in characters).strip()
    return (
        f"Cover illustration for a black-and-white {story.genre} manga. Story: {story.logline} "
        f"Show exactly these characters together in a dramatic, eye-catching hero composition: {names}. "
        f"{looks}\n"
        f"Draw ONLY these characters and no other character, person, hand or creature. "
        "Black and white manga ink style, screentone shading, bold dynamic composition, dramatic lighting. "
        "Keep every character exactly as in their reference sheet; add no claws, fangs or features the "
        "reference does not show. Keep the top quarter of the image fairly open (sky, wall or simple "
        "background) and the bottom tenth simple, because a title and tagline are lettered there later. "
        "Do NOT draw any text at all: no title, letters, words, logos or sound effects."
    )


class ImageGenerator(ABC):
    usage_sink = None  # set by the orchestrator: callable(UsageRecord)

    def generate_cover(self, story: StoryArc, characters: list[CharacterProfile], output_path: Path) -> Path | None:
        """Render cover art (no lettering). Backends that can't return None and the compositor
        falls back to a character sheet."""
        return None

    def generate_character_sheet(self, character: CharacterProfile, output_path: Path) -> Path | None:
        """Render a clean reference sheet for a character. Backends that can't do this return
        None and panels fall back to the raw reference photo."""
        return None

    @abstractmethod
    def generate_panel(self, panel: Panel, characters: list[CharacterProfile], output_path: Path) -> Path:
        """Render one panel to output_path and return it."""


class MockImageGenerator(ImageGenerator):
    """Zero-dependency, zero-cost stand-in: draws the prompt text onto a placeholder canvas.

    Exists so the rest of the pipeline (story -> script -> layout -> PDF) can be built, run,
    and tested without an image-gen API key or network access. See DESIGN.md, Stage 4.
    """

    CANVAS_SIZES = {"square": (768, 768), "landscape": (1152, 768), "portrait": (768, 1152)}

    def generate_character_sheet(self, character: CharacterProfile, output_path: Path) -> Path | None:
        image = Image.new("RGB", (1536, 1024), (245, 245, 245))
        draw = ImageDraw.Draw(image)
        draw.rectangle([8, 8, 1528, 1016], outline="black", width=4)
        draw.text((40, 40), f"[mock character sheet] {character.name}\n{character.visual_description}", fill="black")
        if character.reference_image_path and Path(character.reference_image_path).exists():
            photo = ImageOps.exif_transpose(Image.open(character.reference_image_path)).convert("RGB")
            photo.thumbnail((900, 900))
            image.paste(photo, (60, 100))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(output_path)
        return output_path

    def generate_panel(self, panel: Panel, characters: list[CharacterProfile], output_path: Path) -> Path:
        prompt = build_panel_prompt(panel, characters)
        size = self.CANVAS_SIZES.get(panel.orientation, self.CANVAS_SIZES["square"])
        image = Image.new("RGB", size, color=(235, 235, 235))
        draw = ImageDraw.Draw(image)
        draw.rectangle([4, 4, size[0] - 4, size[1] - 4], outline=(0, 0, 0), width=4)
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


class OpenAIImageGenerator(ImageGenerator):
    """OpenAI Images backend.

    Panels with characters that have a reference photo go through the edits endpoint, which
    takes the photos as conditioning images (this is what keeps faces consistent); other
    panels use plain generations. Size follows the panel's layout orientation so the compositor
    doesn't have to crop away most of the picture.
    """

    GENERATE_URL = "https://api.openai.com/v1/images/generations"
    EDIT_URL = "https://api.openai.com/v1/images/edits"
    SIZES = {"square": "1024x1024", "landscape": "1536x1024", "portrait": "1024x1536"}
    MAX_REFERENCES = 4

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        input_fidelity: str | None = None,
        reference_max_side: int | None = None,
        quality: str | None = None,
    ):
        self.api_key = settings.openai_api_key if api_key is None else api_key
        self.model = settings.openai_image_model if model is None else model
        self.input_fidelity = settings.openai_input_fidelity if input_fidelity is None else input_fidelity
        self.reference_max_side = settings.openai_reference_max_side if reference_max_side is None else reference_max_side
        self.quality = settings.openai_image_quality if quality is None else quality
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def _references(self, panel: Panel, characters: list[CharacterProfile]) -> list[Path]:
        """Prefer a character's generated sheet (already in the target style) over the raw photo."""
        by_name = {c.name: c for c in characters}
        return self._character_refs([by_name[n] for n in panel.characters if n in by_name])

    def _character_refs(self, characters: list[CharacterProfile]) -> list[Path]:
        paths = []
        for character in characters:
            for candidate in (character.sheet_image_path, character.reference_image_path):
                if candidate and Path(candidate).exists():
                    paths.append(Path(candidate))
                    break
        return paths[: self.MAX_REFERENCES]

    def _render(
        self,
        prompt: str,
        size: str,
        references: list[Path],
        fidelity: str,
        stage: str,
        detail: str,
        output_path: Path,
    ) -> Path:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        started = time.perf_counter()
        if references:
            files = [("image[]", (path.stem + ".jpg", _prepare_reference(path, self.reference_max_side), "image/jpeg")) for path in references]
            response = post_with_retry(
                self.EDIT_URL,
                headers=headers,
                data={"model": self.model, "prompt": prompt, "size": size, "quality": self.quality, "input_fidelity": fidelity},
                files=files,
                timeout=300,
            )
        else:
            response = post_with_retry(
                self.GENERATE_URL,
                headers=headers,
                json={"model": self.model, "prompt": prompt, "size": size, "quality": self.quality},
                timeout=300,
            )
        raise_for_status(response)
        body = response.json()
        if self.usage_sink:
            self.usage_sink(openai_image_record(stage, self.model, body.get("usage"), detail, seconds=time.perf_counter() - started))
        image_data = body.get("data", [{}])[0]
        if image_data.get("b64_json"):
            image_bytes = base64.b64decode(image_data["b64_json"])
        elif image_data.get("url"):
            image_response = requests.get(image_data["url"], timeout=120)
            image_response.raise_for_status()
            image_bytes = image_response.content
        else:
            raise ValueError(f"OpenAI image response did not contain image data: {response.text[:500]}")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(image_bytes)
        return output_path

    def generate_cover(self, story: StoryArc, characters: list[CharacterProfile], output_path: Path) -> Path | None:
        return self._render(
            build_cover_prompt(story, characters), self.SIZES["portrait"], self._character_refs(characters),
            self.input_fidelity, "cover", "cover", output_path,
        )

    def generate_character_sheet(self, character: CharacterProfile, output_path: Path) -> Path | None:
        # The sheet is the source of truth for every later panel, so it gets high input fidelity
        # (one-off cost) even though panels use the cheaper default.
        references = [Path(character.reference_image_path)] if character.reference_image_path and Path(character.reference_image_path).exists() else []
        return self._render(
            build_sheet_prompt(character), self.SIZES["landscape"], references,
            settings.openai_sheet_fidelity, "sheets", character.name, output_path,
        )

    def generate_panel(self, panel: Panel, characters: list[CharacterProfile], output_path: Path) -> Path:
        return self._render(
            build_panel_prompt(panel, characters),
            self.SIZES.get(panel.orientation, self.SIZES["square"]),
            self._references(panel, characters),
            self.input_fidelity, "images", f"panel {panel.panel_number}", output_path,
        )


def _prepare_reference(path: Path, max_side: int = 1024) -> bytes:
    """Downscale a reference photo (phone photos are several MB and image-input tokens are billed
    by size) and normalise EXIF rotation, returning JPEG bytes."""
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def get_image_generator(backend: str | None = None) -> ImageGenerator:
    backend = backend or settings.image_backend
    if backend == "mock":
        return MockImageGenerator()
    if backend == "stability":
        return StabilityAIImageGenerator()
    if backend == "openai":
        return OpenAIImageGenerator()
    raise ValueError(f"Unknown image backend: {backend}")
