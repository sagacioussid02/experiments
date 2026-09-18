from __future__ import annotations

from anthropic import Anthropic

from app.config import settings
from app.models import CharacterProfile, ComicPage, StoryArc

SYSTEM_PROMPT = (
    "You are a manga storyboard artist. Break a story arc into concrete pages and panels. "
    "Aim for 3-6 panels per page. Every panel needs a scene_description specific enough for "
    "an illustrator to draw it without further context (setting, character poses/expressions, "
    "camera framing). Keep dialogue short -- comic bubbles have little room."
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


class ScriptGenerator:
    def __init__(self, client: Anthropic | None = None, model: str | None = None):
        self.client = client or Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.claude_model

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
            "\n\nStoryboard this into pages and panels."
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[_SCRIPT_TOOL],
            tool_choice={"type": "tool", "name": "emit_script"},
        )
        tool_use = next(block for block in response.content if block.type == "tool_use")
        return [ComicPage.model_validate(page) for page in tool_use.input["pages"]]
