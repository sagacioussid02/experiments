from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class BibleMarker(BaseModel):
    feature: str = Field(description="e.g. 'Nose', 'Chest patch', 'Ears'")
    description: str = Field(description="Concrete, checkable description: shape, colour, relative size, position.")


class CharacterBible(BaseModel):
    """The locked, editable design of a character. Extracted once from the product photos, edited and
    approved by a human, then injected into every prompt and used to build the judge's checklist."""

    summary: str = ""
    markers: list[BibleMarker] = Field(default_factory=list)
    colours: dict[str, str] = Field(default_factory=dict, description="Real colours, kept for future colour episodes.")
    expression_notes: str = Field(default="", description="How expressions must be drawn (e.g. shouting = plain open oval mouth, no teeth).")
    never: list[str] = Field(default_factory=list, description="Things this character must never have or show.")
    forbidden_words: list[str] = Field(default_factory=list, description="Words the script must not use when describing this character.")
    version: int = 1
    approved: bool = False


class CharacterProfile(BaseModel):
    id: str
    name: str
    backstory: str
    personality: str = ""
    visual_description: str = Field(
        default="",
        description=(
            "Literal appearance description (hair, outfit, distinguishing features) used to "
            "keep the character visually consistent across panels. Kept separate from "
            "backstory/personality because the image generator consumes this field only."
        ),
    )
    reference_image_path: Optional[str] = None
    sheet_image_path: Optional[str] = Field(
        default=None,
        description="Generated manga character sheet; used instead of the raw photo as the panel reference.",
    )
    main: bool = Field(default=False, description="Gets a dedicated 'Character File' page at the front.")
    bible: Optional[CharacterBible] = None


class StoryArc(BaseModel):
    title: str
    genre: str
    logline: str
    synopsis: str
    chapters: list[str] = Field(description="Ordered beat-by-beat summary, one per chapter/act.")


class DialogueLine(BaseModel):
    character: str
    text: str
    bubble_type: Literal["speech", "thought", "shout"] = "speech"


class Panel(BaseModel):
    panel_number: int
    characters: list[str]
    scene_description: str
    camera_angle: str = "medium shot"
    dialogue: list[DialogueLine] = Field(default_factory=list)
    caption: Optional[str] = None
    importance: int = Field(
        default=1,
        ge=1,
        le=3,
        description="1 = normal beat, 2 = emphasized, 3 = splash (gets a full-width, taller row).",
    )
    orientation: Literal["square", "landscape", "portrait"] = "square"
    image_path: Optional[str] = None


class ComicPage(BaseModel):
    page_number: int
    panels: list[Panel]


class UsageRecord(BaseModel):
    """One billable API call. cost_usd is None when the model has no known price."""

    stage: Literal["story", "script", "bible", "sheets", "cover", "images", "qa"]
    provider: Literal["anthropic", "openai"]
    model: str
    detail: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    image_input_tokens: int = 0
    images: int = 0
    cost_usd: Optional[float] = None


class ComicProject(BaseModel):
    id: str
    characters: list[CharacterProfile]
    theme: Optional[str] = None
    story: Optional[StoryArc] = None
    pages: list[ComicPage] = Field(default_factory=list)
    cover_image_path: Optional[str] = None
    usage: list[UsageRecord] = Field(default_factory=list)
    status: Literal["created", "story_ready", "script_ready", "sheets_ready", "images_ready", "composed"] = "created"
