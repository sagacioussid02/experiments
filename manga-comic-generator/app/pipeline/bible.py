from __future__ import annotations

import base64
import re
from pathlib import Path

from anthropic import Anthropic

from app.config import settings
from app.models import CharacterBible, CharacterProfile, ComicPage
from app.usage import anthropic_record

_BIBLE_TOOL = {
    "name": "emit_character_bible",
    "description": "Return the design bible for this character.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence: what this character is."},
            "markers": {
                "type": "array",
                "description": "6-10 identity markers, most recognisable first.",
                "items": {
                    "type": "object",
                    "properties": {"feature": {"type": "string"}, "description": {"type": "string"}},
                    "required": ["feature", "description"],
                },
            },
            "colours": {"type": "object", "additionalProperties": {"type": "string"}},
            "expression_notes": {"type": "string"},
            "never": {"type": "array", "items": {"type": "string"}},
            "forbidden_words": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["summary", "markers", "colours", "expression_notes", "never", "forbidden_words"],
    },
}

SYSTEM_PROMPT = (
    "You document the exact design of a physical handmade product (a knitted or crocheted character) so an "
    "illustrator can draw it identically in every comic panel. Look only at the character itself; ignore hands, "
    "other objects, the backdrop and any other toy in the photo. Be concrete and checkable: give shapes, "
    "colours, RELATIVE sizes (e.g. 'about a third of the muzzle width') and exact positions. For left/right, "
    "say 'on the character's left (viewer's right)'. Describe design, not yarn texture. Include every "
    "distinguishing feature a buyer would recognise: patches, stitched features, ears, eyes, nose/trunk, "
    "proportions. `never` lists things the real product does not have that an illustrator might invent for "
    "an angry or dramatic pose (teeth, fangs, claws, a tail, clothing...). `forbidden_words` are the words a "
    "script writer must not use when describing this character (include singular and irregular plural forms, "
    "e.g. 'tooth' and 'teeth'). `expression_notes` says how emotions must be drawn without breaking the design."
)


def bible_prompt_block(character: CharacterProfile) -> str:
    """Positive-wording design rules for image prompts; empty if the character has no bible."""
    b = character.bible
    if not b or not b.markers:
        return ""
    lines = [f"{character.name} design rules (must hold at every angle and in every expression):"]
    lines += [f"- {m.feature}: {m.description}" for m in b.markers]
    if b.expression_notes:
        lines.append(f"- Expressions: {b.expression_notes}")
    if b.never:
        lines.append(f"- {character.name} NEVER has: " + "; ".join(b.never))
    return "\n".join(lines)


def bible_checks(character: CharacterProfile) -> list[str] | None:
    """The judge's checklist for a character: one check per marker plus a 'nothing added' check.
    None if there is no bible (the judge then uses its generic defaults)."""
    b = character.bible
    if not b or not b.markers:
        return None
    checks = [f"{m.feature}: {m.description}" for m in b.markers]
    if b.never:
        checks.append("Nothing added: none of -- " + "; ".join(b.never))
    return checks


def script_rules(characters: list[CharacterProfile]) -> str:
    parts = []
    for c in characters:
        b = c.bible
        if not b:
            continue
        line = f"- {c.name}: {b.summary}"
        if b.expression_notes:
            line += f" Show emotion like this: {b.expression_notes}"
        if b.forbidden_words:
            line += " NEVER use these words in scene_description or caption: " + ", ".join(b.forbidden_words) + "."
        parts.append(line)
    return "\n".join(parts)


def find_forbidden(pages: list[ComicPage], characters: list[CharacterProfile]) -> list[tuple[int, int, str]]:
    """(page, panel, word) for every forbidden word used in a panel's scene_description or caption
    by a character present in that panel."""
    by_name = {c.name: c for c in characters}
    hits = []
    for page in pages:
        for panel in page.panels:
            text = f"{panel.scene_description} {panel.caption or ''}"
            seen = set()
            for name in panel.characters:
                bible = by_name[name].bible if name in by_name else None
                for word in (bible.forbidden_words if bible else []):
                    if word.lower() in seen:
                        continue
                    if re.search(rf"\b{re.escape(word)}(s|es|ed|ing)?\b", text, re.IGNORECASE):
                        seen.add(word.lower())
                        hits.append((page.page_number, panel.panel_number, word))
    return hits


class BibleExtractor:
    def __init__(self, client: Anthropic | None = None, model: str | None = None):
        self.client = client or Anthropic(api_key=settings.anthropic_api_key)
        self.model = model or settings.claude_model
        self.usage_sink = None

    def extract(self, character: CharacterProfile, photo_path: str) -> CharacterBible:
        from app.pipeline.image_generator import _prepare_reference  # lazy: avoids an import cycle

        image = base64.standard_b64encode(_prepare_reference(Path(photo_path), 1568)).decode()
        prompt = (
            f"Character: {character.name}. Owner's description: {character.visual_description or 'none given'}. "
            "Write the design bible for this character from the photo."
        )
        response = self.client.messages.create(
            model=self.model,
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image}},
                {"type": "text", "text": prompt},
            ]}],
            tools=[_BIBLE_TOOL],
            tool_choice={"type": "tool", "name": "emit_character_bible"},
        )
        if self.usage_sink and getattr(response, "usage", None):
            self.usage_sink(anthropic_record("bible", self.model, response.usage, character.name))
        tool_use = next(block for block in response.content if block.type == "tool_use")
        bible = CharacterBible.model_validate(tool_use.input)
        if not bible.summary.strip():  # the model sometimes leaves it blank
            bible.summary = character.visual_description or f"{character.name}, a handmade character."
        return bible
