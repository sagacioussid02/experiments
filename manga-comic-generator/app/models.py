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
    image_path: Optional[str] = None


class ComicPage(BaseModel):
    page_number: int
    panels: list[Panel]


class ComicProject(BaseModel):
    id: str
    characters: list[CharacterProfile]
    theme: Optional[str] = None
    story: Optional[StoryArc] = None
    pages: list[ComicPage] = Field(default_factory=list)
    status: Literal["created", "story_ready", "script_ready", "images_ready", "composed"] = "created"
