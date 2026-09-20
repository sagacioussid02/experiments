from __future__ import annotations

import base64
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageOps
from pydantic import BaseModel

from app.config import settings
from app.http import post_with_retry, raise_for_status
from app.models import CharacterProfile
from app.pipeline.bible import bible_checks
from app.usage import openai_chat_record

# Fixed DESIGN features only. Expression and pose are allowed to vary (see ALLOWED_TO_VARY), so a
# happy or sleepy face is not a failure. Character-specific, measurable versions of these come from
# the character bible; these are the generic fallbacks.
MINOR_PREFIX = "MINOR: "

DEFAULT_CHECKS = [
    "Eye design: same shape, size, colour and spacing as the reference (ignore expression: lids, brows, gaze)",
    "Nose/muzzle/trunk: same shape, colour and relative size as the reference at this angle -- not smaller, pointier or larger",
    "Ears: same shape, size and placement",
    "Markings/patches: same shape, colour and position on the body as the reference sheet",
    "Body proportions and silhouette match the reference",
    "Nothing added that the reference does not show: no teeth, fangs, claws, tail, extra limbs, clothing or accessories",
]

ALLOWED_TO_VARY = (
    "ALLOWED TO VARY -- never fail a check for these: facial expression (eyelids, brows, gaze, mouth open, "
    "closed or smiling, as long as no teeth are added), pose, camera angle and distance, lighting, and "
    "the character being partly hidden or cropped."
)

SYSTEM_PROMPT = (
    "You are a strict quality reviewer for illustrated characters in a comic that is sold alongside a "
    "physical handmade product. You are given, for each character that should appear, an approved "
    "REFERENCE SHEET (the standard for the design) and for some, PHOTOS of the real product (context), "
    "then one PANEL to review. Judge only whether each character in the panel keeps the same DESIGN as "
    "its reference sheet -- shapes, proportions, markings, colours-as-tones -- not artistic quality. "
    "Rendering style differs from photos (ink and screentone vs yarn), so compare features and shapes, "
    "never texture. Be literal about design: if a fixed feature is smaller, larger, a different shape, in a "
    "different place, or missing/added compared with the reference sheet, that check fails; do not give the "
    "benefit of the doubt. But expression and pose are supposed to change from panel to panel and must "
    "never cause a failure. Answer every listed check for every listed character, using the check text "
    "as its name. Checks whose text starts with 'MINOR:' are fine details: still answer them honestly, "
    "they are advisory."
)

SHEET_SYSTEM_PROMPT = (
    "You are a strict quality reviewer for a character reference sheet that will be copied into every panel "
    "of a comic sold alongside a physical handmade product. You are given PHOTOS of the real product (the "
    "ground truth for the design) and a generated black-and-white manga SHEET showing several views. Every "
    "view on the sheet must keep the product's design: shapes, proportions, markings, features and their "
    "positions -- rendering style differs from the photo (ink and screentone vs yarn), so compare shapes, "
    "never texture. Be literal: a feature that is smaller, larger, a different shape, in a different place, "
    "or added/missing compared with the photo fails its check. Idealising or 'cleaning up' a distinctive "
    "feature (for example turning an irregular patch into a neat symbol) is a failure. Expression and pose "
    "may differ between views. Report the character once (visible=true). Answer every listed check using "
    "the check text as its name; set unlisted_characters if any other character or hand is drawn, and "
    "text_in_art if any labels or words appear on the sheet."
)


_SCHEMA = {
    "type": "object",
    "properties": {
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "visible": {"type": "boolean", "description": "Is this character visible in the panel?"},
                    "likeness": {"type": "integer", "description": "1 (not the same character) to 5 (identical design)"},
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "passed": {"type": "boolean"},
                                "note": {"type": "string", "description": "What differs, or 'ok'"},
                            },
                            "required": ["name", "passed", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "visible", "likeness", "checks"],
                "additionalProperties": False,
            },
        },
        "unlisted_characters": {"type": "boolean", "description": "Any person, hand, creature or character NOT in the listed cast?"},
        "text_in_art": {"type": "boolean", "description": "Any letters, words or sound-effect lettering drawn in the artwork itself?"},
        "summary": {"type": "string"},
    },
    "required": ["characters", "unlisted_characters", "text_in_art", "summary"],
    "additionalProperties": False,
}


class CheckResult(BaseModel):
    name: str
    passed: bool
    note: str


class CharacterReview(BaseModel):
    name: str
    visible: bool
    likeness: int
    checks: list[CheckResult]


class PanelReview(BaseModel):
    characters: list[CharacterReview]
    unlisted_characters: bool
    text_in_art: bool
    summary: str
    strict_names: list[str] = []

    def warnings(self) -> list[str]:
        """Failed MINOR checks: advisory only, they never fail a panel or trigger a redraw."""
        return [f"{c.name}: {k.name} -- {k.note}" for c in self.characters if c.visible for k in c.checks if k.name.startswith(MINOR_PREFIX) and not k.passed]

    def problems(self) -> list[str]:
        out = []
        for c in self.characters:
            if not c.visible:
                continue
            strict = c.name in self.strict_names
            # The 1-5 score is subjective (it flagged many good panels in our labelled set), so it only
            # catches gross failures; the concrete per-feature checks below are the real gate.
            if c.likeness <= (2 if strict else 1):
                out.append(f"{c.name}: likeness {c.likeness}/5")
            if strict:  # main character: every individual check must pass
                out += [f"{c.name}: {k.name} -- {k.note}" for k in c.checks if not k.passed and not k.name.startswith(MINOR_PREFIX)]
        if self.unlisted_characters:
            out.append("unlisted character/person/hand in frame")
        if self.text_in_art:
            out.append("text drawn in the artwork")
        return out

    @property
    def passed(self) -> bool:
        return not self.problems()


def merge_reviews(reviews: list[PanelReview]) -> PanelReview:
    """Strictest-wins merge of several reviews of the same panel: a check fails if ANY run failed it
    (notes concatenated), likeness is the minimum, and unlisted-character/text flags are OR-ed."""
    first = reviews[0]
    merged = []
    for c in first.characters:
        runs = [x for r in reviews for x in r.characters if x.name == c.name and x.visible] or [c]
        checks: dict[str, CheckResult] = {}
        for run in runs:
            for k in run.checks:
                prev = checks.get(k.name)
                if prev is None or (prev.passed and not k.passed):
                    checks[k.name] = k
                elif not prev.passed and not k.passed and k.note not in prev.note:
                    checks[k.name] = CheckResult(name=k.name, passed=False, note=f"{prev.note} | {k.note}")
        merged.append(CharacterReview(name=c.name, visible=any(x.visible for x in runs), likeness=min(x.likeness for x in runs), checks=list(checks.values())))
    return PanelReview(
        characters=merged,
        unlisted_characters=any(r.unlisted_characters for r in reviews),
        text_in_art=any(r.text_in_art for r in reviews),
        summary=" || ".join(r.summary for r in reviews),
        strict_names=first.strict_names,
    )


def _data_url(path: Path, max_side: int = 1024) -> str:
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


def _image(path: Path, max_side: int = 1024) -> dict:
    return {"type": "image_url", "image_url": {"url": _data_url(path, max_side), "detail": "high"}}


def _crop_url(path: Path, box: tuple[float, float, float, float], pad: float = 0.25, target: int = 900) -> str:
    """Zoomed crop of a normalised (x0,y0,x1,y1) box from the full-resolution panel, padded and
    upscaled so small details (a nose, a mouth) get many more pixels than in the whole panel."""
    image = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    w, h = image.size
    x0, y0, x1, y1 = box
    bw, bh = (x1 - x0) * w, (y1 - y0) * h
    left, top = max(0, int(x0 * w - bw * pad)), max(0, int(y0 * h - bh * pad))
    right, bottom = min(w, int(x1 * w + bw * pad)), min(h, int(y1 * h + bh * pad))
    crop = image.crop((left, top, right, bottom))
    scale = target / max(crop.size)
    if scale > 1:
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
    buffer = io.BytesIO()
    crop.save(buffer, "JPEG", quality=90)
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode()


_BOX_SCHEMA = {
    "type": "object",
    "properties": {
        "visible": {"type": "boolean"},
        "x0": {"type": "number"}, "y0": {"type": "number"}, "x1": {"type": "number"}, "y1": {"type": "number"},
    },
    "required": ["visible", "x0", "y0", "x1", "y1"],
    "additionalProperties": False,
}


class PanelJudge:
    """Independent visual reviewer (an OpenAI vision model, deliberately not the Claude model that
    wrote the story) that checks a panel against the approved sheets. Main characters (strict) must
    pass every check with likeness >= 4; supporting characters only need likeness >= 3."""

    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        native_res: bool = True,
        face_crops: bool = True,
        locator_model: str | None = None,
        box_cache: dict | None = None,
    ):
        self.api_key = settings.openai_api_key if api_key is None else api_key
        self.model = model or settings.judge_model
        self.reasoning_effort = reasoning_effort or settings.judge_reasoning_effort
        self.native_res = native_res  # send the panel at full resolution (else shrunk to 1024px)
        self.face_crops = face_crops  # also send a zoomed crop of each strict character's head
        self.locator_model = locator_model or settings.locator_model
        self.box_cache = {} if box_cache is None else box_cache  # share between judges to avoid re-locating
        self.usage_sink = None
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")

    def _post(self, body: dict):
        return post_with_retry(self.URL, headers={"Authorization": f"Bearer {self.api_key}"}, json=body, timeout=300)

    def locate_head(self, panel_image: Path, character: CharacterProfile, detail: str = "") -> tuple[float, float, float, float] | None:
        """Ask a small, cheap vision model where a character's head is (normalised box), or None."""
        key = (str(panel_image), character.name)
        if key in self.box_cache:
            return self.box_cache[key]
        body = {
            "model": self.locator_model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": (
                    f"In this comic panel, find the HEAD of the character {character.name} ({character.visual_description or 'see panel'}), "
                    "including ears, eyes, nose and mouth. Return a tight bounding box as fractions of the image width/height "
                    "(x0,y0 top-left; x1,y1 bottom-right, each 0..1). If the character is not visible, visible=false and zeros.")},
                _image(panel_image, 1024),
            ]}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "head_box", "strict": True, "schema": _BOX_SCHEMA}},
            "reasoning_effort": "minimal",
        }
        data = self._chat(body, "qa", f"locate {detail}")
        try:
            box = json.loads(data["choices"][0]["message"]["content"])
        except (json.JSONDecodeError, TypeError, KeyError):
            return None
        if not box.get("visible") or not (0 <= box["x0"] < box["x1"] <= 1 and 0 <= box["y0"] < box["y1"] <= 1):
            self.box_cache[key] = None
            return None
        self.box_cache[key] = (box["x0"], box["y0"], box["x1"], box["y1"])
        return self.box_cache[key]

    def _chat(self, body: dict, stage: str, detail: str) -> dict:
        started = time.perf_counter()
        response = self._post(body)
        raise_for_status(response)
        data = response.json()
        if self.usage_sink:
            self.usage_sink(openai_chat_record(stage, body["model"], data.get("usage"), detail, seconds=time.perf_counter() - started))
        return data

    def review(
        self,
        panel_image: Path,
        characters: list[CharacterProfile],
        strict_names: list[str],
        checks: dict[str, list[str]] | None = None,
        detail: str = "",
    ) -> PanelReview:
        content: list[dict] = [{"type": "text", "text": ALLOWED_TO_VARY + "\n\nCAST FOR THIS PANEL (only these characters are allowed):"}]
        for c in characters:
            listed = (checks or {}).get(c.name) or bible_checks(c) or DEFAULT_CHECKS
            content.append({"type": "text", "text": f"\n### {c.name}\nDescription: {c.visual_description or 'n/a'}\nChecks:\n" + "\n".join(f"- {k}" for k in listed) + "\nREFERENCE SHEET:"})
            sheet = c.sheet_image_path or c.reference_image_path
            if sheet and Path(sheet).exists():
                content.append(_image(Path(sheet)))
            if c.name in strict_names and c.reference_image_path and Path(c.reference_image_path).exists() and c.sheet_image_path:
                content += [{"type": "text", "text": f"REAL PRODUCT PHOTO of {c.name} (ground truth for the design):"}, _image(Path(c.reference_image_path))]
        panel_side = 2048 if self.native_res else 1024
        content += [{"type": "text", "text": "\nPANEL TO REVIEW:"}, _image(panel_image, panel_side)]
        if self.face_crops:
            for c in characters:
                if c.name in strict_names:
                    box = self.locate_head(panel_image, c, detail)
                    if box:
                        content += [
                            {"type": "text", "text": f"CLOSE-UP CROP of {c.name}'s head from this same panel (judge nose, eyes and mouth details here):"},
                            {"type": "image_url", "image_url": {"url": _crop_url(panel_image, box), "detail": "high"}},
                        ]

        return self._submit(SYSTEM_PROMPT, content, strict_names, detail)

    def _submit(self, system: str, content: list[dict], strict_names: list[str], detail: str) -> PanelReview:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "panel_review", "strict": True, "schema": _SCHEMA}},
            "reasoning_effort": self.reasoning_effort,
        }
        data = self._chat(body, "qa", detail)
        message = data["choices"][0]["message"]
        if message.get("refusal") or not message.get("content"):
            raise ValueError(f"Judge returned no review: {message.get('refusal') or data['choices'][0].get('finish_reason')}")
        try:
            review = PanelReview.model_validate(json.loads(message["content"]))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Judge returned an invalid review: {message['content'][:300]!r}") from exc
        review.strict_names = strict_names
        return review

    def review_sheet(self, sheet_path: Path, character: CharacterProfile, detail: str = "") -> PanelReview:
        """Check a generated character sheet (several views) against the REAL product photo, which is
        the ground truth. Catches drift at the source, before any panel copies it."""
        checks = bible_checks(character) or DEFAULT_CHECKS
        content: list[dict] = [
            {"type": "text", "text": ALLOWED_TO_VARY + f"\n\n### {character.name}\nDescription: {character.visual_description or 'n/a'}\nChecks (apply to EVERY view on the sheet):\n" + "\n".join(f"- {k}" for k in checks)},
        ]
        if character.reference_image_path and Path(character.reference_image_path).exists():
            content += [{"type": "text", "text": "REAL PRODUCT PHOTO (ground truth; ignore any hand, other objects or backdrop in it):"}, _image(Path(character.reference_image_path))]
        content += [{"type": "text", "text": "\nGENERATED CHARACTER SHEET TO REVIEW (several views of the same character):"}, _image(sheet_path, 2048)]
        return self._submit(SHEET_SYSTEM_PROMPT, content, [character.name], detail or f"sheet {character.name}")
