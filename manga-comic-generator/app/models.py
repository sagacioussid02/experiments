from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


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

    stage: Literal["story", "script", "sheets", "cover", "images"]
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
