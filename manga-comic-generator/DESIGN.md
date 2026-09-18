# Design: Character → Manga Comic Pipeline

## Goal

Given N characters (a photo + a backstory each) and an optional theme, produce a finished
multi-page comic: a story, dialogue, panel artwork, and a laid-out, readable page with speech
bubbles — exported as a PDF.

## Why a pipeline instead of one big prompt

A single "generate the whole comic" prompt can't call an image model, can't be partially
retried, and gives you nothing to inspect or edit between steps. Splitting into stages buys:

- **Independent retry/cost profiles.** Text generation (Claude) is cheap and fast; image
  generation is slow and costs real money per call. Treating them as separate, persisted
  steps means a bad *story* never burns image-gen budget, and a bad *panel image* doesn't
  force you to regenerate the story.
- **Swappable backends per stage.** The image-generation vendor landscape moves fast
  (Stability, Imagen, GPT-image, self-hosted Stable Diffusion + IP-Adapter/InstantID). If
  image generation is isolated behind one interface, the story/script/layout code never
  changes when you swap it.
- **Inspectable intermediate state.** The story arc and panel script are both plain data
  (pydantic models, persisted as JSON) you can read, hand-edit, or regenerate independently —
  useful both for debugging and for eventually adding a human-in-the-loop review step.

## Stage 1 — Character intake (`app/models.py: CharacterProfile`)

`visual_description` is a separate field from `backstory`, even though both describe the
character, because they're consumed by different things: `backstory`/`personality` feed the
*story* generator (which reasons about motivation and conflict), while `visual_description`
feeds the *image* generator (which needs a literal, repeatable description — hair color,
outfit, species — not narrative). Keeping them separate means each downstream stage only sees
the fields relevant to it, and the visual description can be reused verbatim in every panel
prompt without duplicating prose that was written for a different purpose.

## Stage 2 — Story generation (`app/pipeline/story_generator.py`)

Claude is called with a **forced tool call** (`tool_choice={"type": "tool", "name":
"emit_story_arc"}`) rather than asked to return JSON in prose. Why: JSON embedded in prose
breaks in predictable ways — markdown fences, trailing commentary, truncation mid-object.
Forcing a tool call makes the SDK hand back an already-parsed, schema-shaped dict directly
from the API's structured tool-input mechanism, and pydantic validation (`StoryArc.model_validate`)
is a second safety net on top of that. This pattern repeats in every stage that needs
structured output from an LLM.

## Stage 3 — Script / panel breakdown (`app/pipeline/script_generator.py`)

The atomic unit is the **panel**, not the page. Real comics vary panel count per page for
pacing (a full-page splash for a climax vs. six small panels for a rapid exchange), so asking
the LLM for a fixed grid up front would fight against good pacing. Instead, the LLM emits
pages each containing a variable number of panels, and the layout stage adapts its grid to
whatever count it's handed (see Stage 5).

The character appearance sheet (each character's `visual_description`) is re-sent in this
prompt too, so panel `scene_description`s reference consistent appearance details — this
matters because the image generator's prompt builder (Stage 4) also re-injects the same
sheet per panel; the two stages agreeing on the same source of truth is what keeps a
character's description from drifting across panels.

## Stage 4 — Panel image generation (`app/pipeline/image_generator.py`)

`ImageGenerator` is an abstract base class with one method, `generate_panel`. Two
implementations exist:

- **`MockImageGenerator`** — zero API keys, zero network calls, fully deterministic: it
  draws the prompt text onto a placeholder canvas with Pillow. This is not a toy left over
  from testing — it's what lets you build and exercise the *entire* pipeline (story → script
  → layout → PDF) before you've decided on, or paid for, a real image backend. Always build
  the fake for the slowest/flakiest/most expensive external dependency before or alongside
  the real one.
- **`StabilityAIImageGenerator`** — a real HTTP integration against Stability AI's
  text-to-image API. It's the first real backend specifically because its API is simple
  (single REST call, no SDK) and well documented, making it a good first target; swapping to
  Imagen, GPT-image, or a self-hosted SD server later is "write one more subclass," not
  "rewrite the pipeline."

**The character-consistency problem:** plain text-to-image regenerates a subtly different-
looking character every panel. Two mitigations, one implemented and one documented as a
next step:
1. *(implemented)* `build_panel_prompt` re-injects each present character's fixed
   `visual_description` into every panel's prompt as a mini "character sheet" — cheap, and
   keeps gross features (hair color, outfit, species) from drifting.
2. *(next step, see `StabilityAIImageGenerator`'s docstring)* image-to-image / IP-Adapter /
   InstantID conditioning on the character's actual reference photo, which is the real fix
   for facial consistency — text prompts alone can't guarantee that.

## Stage 5 — Layout / compositing (`app/pipeline/layout.py`)

Built directly on Pillow rather than, say, rendering HTML/CSS with a headless browser. Panel
layout is simple 2D geometry (grid position, bubble placement, text wrapping) and Pillow gives
direct pixel control with no extra runtime dependency. The trade-off is real: no CSS means
anything fancier than rectangular panels (angled panel borders, bubble tails, bleed panels)
has to be hand-rolled — `_grid_for` is a deliberately simple panel-count → rows/cols heuristic,
not a real manga layout engine, and is called out as a follow-up below.

## Orchestration (`app/pipeline/orchestrator.py`)

`ComicPipeline` exposes both the four stages individually and a `run_all` convenience method.
Every stage persists the `ComicProject` to disk (`app/storage.py`) immediately after it
finishes, with a `status` field tracking how far the project has gotten
(`created → story_ready → script_ready → images_ready → composed`). This means a crash or bad
output partway through never loses already-paid-for LLM/image-gen calls — you re-run only the
stages after the last saved status.

## API layer (`app/main.py`)

FastAPI, for three reasons: request validation reuses the exact same pydantic models the
pipeline already uses internally (no duplicate schema to keep in sync), it's async-native so
slow external calls (Claude, image gen) don't block the event loop, and it gives free
interactive API docs at `/docs` while iterating. The frontend is deliberately plain HTML/CSS/
vanilla JS (`static/`) with no build step, served directly by FastAPI's `StaticFiles` — there's
no reason to add a JS toolchain before the pipeline itself is solid.

## What's real vs. stubbed today

| Stage | Status |
|---|---|
| Character intake | Real |
| Story generation | Real (needs `ANTHROPIC_API_KEY`) |
| Script generation | Real (needs `ANTHROPIC_API_KEY`) |
| Image generation — mock | Real, fully working, no external deps |
| Image generation — Stability AI | Real HTTP integration (needs `STABILITY_API_KEY`); no image-conditioning yet |
| Layout / PDF export | Real, fully working, no external deps |

## Next steps, in priority order

1. Add image-to-image / IP-Adapter conditioning on the character's reference photo for real
   facial consistency (the current text-only mitigation is a floor, not a ceiling).
2. Add a review/edit step between story generation and script generation — the cheapest place
   to put a human in the loop, since nothing expensive (images) has happened yet.
3. Move from flat-file JSON storage to SQLite once this needs to support more than one user
   at a time.
4. Stream pipeline progress to the frontend (SSE) — story + script + N image generations
   together can take 30–90+ seconds, and the current UI just polls.
5. Replace `_grid_for`'s fixed heuristic with panel *sizes* the LLM can suggest (e.g. "this
   panel is the climax, make it a splash"), so pacing genuinely varies.
