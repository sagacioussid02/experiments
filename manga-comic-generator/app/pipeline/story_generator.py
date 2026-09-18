from __future__ import annotations

from anthropic import Anthropic

from app.config import settings
from app.models import CharacterProfile, StoryArc

SYSTEM_PROMPT = (
    "You are a manga story editor. Given a cast of characters, invent a tight, "
    "self-contained story arc suited for a short comic (roughly 4-8 pages). "
    "Favor a clear conflict tied to the characters' backstories over a generic plot."
)

# Forcing this tool (via tool_choice below) makes the API return an already-parsed,
# schema-shaped dict instead of prose containing JSON -- see DESIGN.md, Stage 2.
_STORY_ARC_TOOL = {
    "name": "emit_story_arc",
    "description": "Return the finished story arc.",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "genre": {"type": "string"},
            "logline": {"type": "string", "description": "One sentence pitch."},
            "synopsis": {"type": "string", "description": "3-6 sentence summary of the full arc."},
            "chapters": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered beat-by-beat summary, one entry per chapter/act.",
            },
        },
        "required": ["title", "genre", "logline", "synopsis", "chapters"],
    },
}


class StoryGenerator:
    def __init__(self, client: Anthropic | None = None, model: str | None = None):
        self.client = client or Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.claude_model

    def generate(self, characters: list[CharacterProfile], theme: str | None = None) -> StoryArc:
        cast = "\n\n".join(
            f"- {c.name}\n  Backstory: {c.backstory}\n  Personality: {c.personality or 'unspecified'}"
            for c in characters
        )
        theme_line = theme or "No theme given -- infer a genre that best fits the characters' backstories."
        user_prompt = f"Cast:\n{cast}\n\nTheme/genre hint: {theme_line}\n\nInvent the story arc."

        response = self.client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[_STORY_ARC_TOOL],
            tool_choice={"type": "tool", "name": "emit_story_arc"},
        )
        tool_use = next(block for block in response.content if block.type == "tool_use")
        return StoryArc.model_validate(tool_use.input)
