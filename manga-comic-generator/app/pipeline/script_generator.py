from __future__ import annotations

import time

import json

from anthropic import Anthropic
from pydantic import ValidationError

from app.config import settings
from app.pipeline.bible import find_forbidden, script_rules
from app.usage import anthropic_record
from app.models import CharacterProfile, ComicPage, StoryArc

SYSTEM_PROMPT = (
    "You are a manga storyboard artist. Break a story arc into concrete pages and panels. "
    "Aim for 3-6 panels per page. Every panel needs a scene_description specific enough for "
    "an illustrator to draw it without further context (setting, character poses/expressions, "
    "camera framing). Keep dialogue short -- at most 2 lines per panel and about 12 words per line, "
    "since bubbles have little room. List each panel's `characters` in left-to-right order of "
    "where they stand in the frame; bubble tails are placed from that order. Set `importance` "
    "to 1 for ordinary beats, 2 for emphasis, and 3 (at most one per page, at most one every "
    "few pages) for a splash panel at a climax or reveal. Never put dialogue in "
    "scene_description; use the dialogue and caption fields. scene_description must describe only "
    "what is visible: never mention sound effects, onomatopoeia, or lettering (the illustrator is "
    "told not to draw any text), and only include characters listed in that panel's `characters`."
)

_SCRIPT_TOOL = {
    "name": "emit_script",
    "description": "Return the full page-by-page, panel-by-panel script.",
    "input_schema": {
        "type": "object",
        "properties": {
            "pages": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "page_number": {"type": "integer"},
                        "panels": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "panel_number": {"type": "integer"},
                                    "characters": {"type": "array", "items": {"type": "string"}},
                                    "scene_description": {"type": "string"},
                                    "camera_angle": {"type": "string"},
                                    "importance": {"type": "integer", "enum": [1, 2, 3]},
                                    "caption": {"type": ["string", "null"]},
                                    "dialogue": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "character": {"type": "string"},
                                                "text": {"type": "string"},
                                                "bubble_type": {
                                                    "type": "string",
                                                    "enum": ["speech", "thought", "shout"],
                                                },
                                            },
                                            "required": ["character", "text"],
                                        },
                                    },
                                },
                                "required": [
                                    "panel_number",
                                    "characters",
                                    "scene_description",
                                    "camera_angle",
                                ],
                            },
                        },
                    },
                    "required": ["page_number", "panels"],
                },
            }
        },
        "required": ["pages"],
    },
}


_FIX_TOOL = {
    "name": "emit_fixes",
    "description": "Return rewritten scene descriptions for the listed panels.",
    "input_schema": {
        "type": "object",
        "properties": {"fixes": {"type": "array", "items": {"type": "object", "properties": {
            "page_number": {"type": "integer"}, "panel_number": {"type": "integer"},
            "scene_description": {"type": "string"}, "caption": {"type": ["string", "null"]},
        }, "required": ["page_number", "panel_number", "scene_description"]}}},
        "required": ["fixes"],
    },
}


class ScriptGenerator:
    def __init__(self, client: Anthropic | None = None, model: str | None = None):
        self.client = client or Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.claude_model
        self.usage_sink = None  # set by the orchestrator: callable(UsageRecord)

    def generate(self, story: StoryArc, characters: list[CharacterProfile]) -> list[ComicPage]:
        # Re-sending the appearance sheet here (in addition to inside the image-gen prompt
        # builder) keeps scene_descriptions and panel-image prompts working off the same
        # source of truth -- see DESIGN.md, Stage 3.
        appearance_sheet = "\n".join(f"- {c.name}: {c.visual_description}" for c in characters)
        chapters = "\n".join(f"- {chapter}" for chapter in story.chapters)
        user_prompt = (
            f"Title: {story.title}\nGenre: {story.genre}\nSynopsis: {story.synopsis}\n"
            f"Chapters:\n{chapters}\n\n"
            f"Character appearance sheet (keep these consistent in every scene_description):\n{appearance_sheet}"
            + (f"\n\nDesign rules from the character bibles (obey strictly):\n{script_rules(characters)}" if script_rules(characters) else "")
            + "\n\nStoryboard this into pages and panels."
        )
        started = time.perf_counter()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[_SCRIPT_TOOL],
            tool_choice={"type": "tool", "name": "emit_script"},
        )
        if self.usage_sink and getattr(response, "usage", None):
            self.usage_sink(anthropic_record("script", self.model, response.usage, seconds=time.perf_counter() - started))
        tool_use = next(block for block in response.content if block.type == "tool_use")

        raw_input = tool_use.input
        if isinstance(raw_input, str):
            try:
                raw_input = json.loads(raw_input)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Anthropic returned a non-JSON script payload: {raw_input!r}") from exc

        if not isinstance(raw_input, dict):
            raise ValueError(f"Anthropic tool call did not return a dict payload: {raw_input!r}")

        pages = raw_input.get("pages")
        if not isinstance(pages, list):
            raise ValueError(
                "Anthropic tool call is missing the required 'pages' list. "
                f"Received payload: {raw_input!r}"
            )

        try:
            parsed = [ComicPage.model_validate(page) for page in pages]
        except ValidationError as exc:
            raise ValueError(f"Anthropic returned invalid page schema: {exc}") from exc
        return self.enforce_forbidden_words(parsed, characters)

    def enforce_forbidden_words(self, pages: list[ComicPage], characters: list[CharacterProfile]) -> list[ComicPage]:
        """Deterministic word check against each character's bible; offending panels get one targeted
        rewrite. Raises if a forbidden word survives, so a bad script never reaches paid image stages."""
        hits = find_forbidden(pages, characters)
        if not hits:
            return pages
        offending = {(pg, pn) for pg, pn, _ in hits}
        words = sorted({w for _, _, w in hits})
        panels = {
            (page.page_number, panel.panel_number): panel for page in pages for panel in page.panels
        }
        listing = "\n".join(
            f"- page {pg} panel {pn}: {panels[(pg, pn)].scene_description}"
            + (f" | caption: {panels[(pg, pn)].caption}" if panels[(pg, pn)].caption else "")
            for pg, pn in sorted(offending)
        )
        started = time.perf_counter()
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system="You fix comic panel descriptions. Keep meaning, drama and framing; only change wording.",
            messages=[{"role": "user", "content": (
                f"Rewrite these panel descriptions so they never use these words (or any form of them): {', '.join(words)}. "
                "Convey the same emotion through eyes, brows, posture and movement instead.\n" + listing
            )}],
            tools=[_FIX_TOOL],
            tool_choice={"type": "tool", "name": "emit_fixes"},
        )
        if self.usage_sink and getattr(response, "usage", None):
            self.usage_sink(anthropic_record("script", self.model, response.usage, "forbidden-word rewrite", seconds=time.perf_counter() - started))
        fixes = next(block for block in response.content if block.type == "tool_use").input.get("fixes", [])
        for fix in fixes:
            panel = panels.get((fix.get("page_number"), fix.get("panel_number")))
            if panel:
                panel.scene_description = fix.get("scene_description", panel.scene_description)
                if panel.caption is not None and "caption" in fix:
                    panel.caption = fix["caption"]
        remaining = find_forbidden(pages, characters)
        if remaining:
            raise ValueError(f"Script still uses forbidden words after a rewrite: {remaining}")
        return pages
