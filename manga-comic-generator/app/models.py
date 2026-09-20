from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class BibleMarker(BaseModel):
    feature: str = Field(description="e.g. 'Nose', 'Chest patch', 'Ears'")
    description: str = Field(description="Concrete, checkable description: shape, colour, relative size, position.")
    critical: bool = Field(default=True, description="False for fine details (highlights, stitch counts): advisory only, never fails a panel.")
    deviates_from_product: bool = Field(default=False, description="Set at sheet approval: the approved sheet draws this feature differently from the real product photo.")


class CharacterBible(BaseModel):
    """The locked, editable design of a character. Extracted once from the product photos, edited and
    approved by a human, then injected into every prompt and used to build the judge's checklist."""

    summary: str = ""
    markers: list[BibleMarker] = Field(default_factory=list)
    colours: dict[str, str] = Field(default_factory=dict, description="Real colours, kept for future colour episodes.")
    expression_notes: str = Field(default="", description="How expressions must be drawn (e.g. shouting = plain open oval mouth, no teeth).")
    never: list[str] = Field(default_factory=list, description="Things this character must never have or show (critical: any of them fails a panel).")
    never_minor: list[str] = Field(default_factory=list, description="Things to avoid but that expressive art tends to add (eyebrows, visible fists): advisory, never fail a panel.")
    forbidden_words: list[str] = Field(default_factory=list, description="Words the script must not use when describing this character.")
    version: int = 1
    approved: bool = False


class MarkerCheck(BaseModel):
    """One bible marker checked against a sheet candidate, twice: against the real photo and against
    the bible's own words. The four combinations tell us whether the sheet or the bible is wrong."""

    marker: str
    critical: bool = True
    matches_photo: bool = True
    matches_bible: bool = True
    sheet_shows: str = ""
    note: str = ""


class CharacterConfidence(BaseModel):
    sheet_critical_failures: int = Field(default=0, description="Critical markers where the approved sheet differs from the real photo.")
    bible_updates: int = Field(default=0, description="Markers rewritten at approval to describe what the sheet shows (info only).")
    first_attempt_pass_rate: Optional[float] = Field(default=None, description="Share of this character's panels that passed QA on the first draw.")
    low: bool = False


class SheetCandidate(BaseModel):
    path: str
    markers: list[MarkerCheck] = Field(default_factory=list)
    text_in_art: bool = False
    unlisted_characters: bool = False
    problems: list[str] = Field(default_factory=list, description="Judge's findings vs the real product photo; fewer is better.")
    checked: bool = False


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
    sheet_candidates: list[SheetCandidate] = Field(default_factory=list)
    sheet_approved: bool = Field(default=False, description="This sheet is frozen and reused for every episode.")
    approval: Optional[Literal["manual", "auto"]] = Field(default=None, description="Who approved the sheet: the owner, or the system (soft gate, no blocking).")
    reconciliation: list[str] = Field(default_factory=list, description="Human-readable log of bible changes made to match the approved sheet.")
    confidence: CharacterConfidence = Field(default_factory=CharacterConfidence)
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
    retry_hint: str = Field(default="", description="Corrections from a failed QA attempt, fed into the next prompt.")
    qa_attempts: int = 0
    qa_passed: Optional[bool] = Field(default=None, description="None = not checked (no judge or judge error).")
    qa_problems: list[str] = Field(default_factory=list)


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
    seconds: Optional[float] = Field(default=None, description="Wall-clock time of the call (includes rate-limit waits and retries).")


class ComicProject(BaseModel):
    id: str
    characters: list[CharacterProfile]
    theme: Optional[str] = None
    story: Optional[StoryArc] = None
    pages: list[ComicPage] = Field(default_factory=list)
    cover_image_path: Optional[str] = None
    qa_breaker: Optional[dict] = Field(default=None, description="Set when the QA circuit breaker tripped during image generation.")
    usage: list[UsageRecord] = Field(default_factory=list)
    status: Literal["created", "story_ready", "script_ready", "sheets_ready", "images_ready", "composed"] = "created"
