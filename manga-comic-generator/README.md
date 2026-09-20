# Manga Comic Generator

Turn a cast of characters (photo + backstory each) into a multi-page manga/comic: story arc →
panel-by-panel script → panel artwork → laid-out pages → PDF.

See [`DESIGN.md`](DESIGN.md) for why each part of the pipeline is built the way it is.

## Pipeline

```
CharacterProfile[]  --(Claude, tool-use)-->  StoryArc
StoryArc + Characters --(Claude, tool-use)-->  ComicPage[] (panels + dialogue)
Characters + photos --(ImageGenerator)-->  one manga character sheet each
StoryArc + sheets  --(ImageGenerator)-->  cover art
ComicPage[].panels + sheets --(ImageGenerator)-->  panel PNGs
cover + character file + pages --(Pillow)-->  page PNGs + comic.pdf  (+ usage.json cost report)
```

## Quick start

```bash
cd manga-comic-generator
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY; leave IMAGE_BACKEND=mock to skip image-gen costs
uvicorn app.main:app --reload
```

Open http://localhost:8000 — add a couple of characters (name, backstory, personality, a short
visual description, optionally a reference photo), pick a theme, and generate. With
`IMAGE_BACKEND=mock` the whole pipeline runs with zero image-gen cost, drawing placeholder
panels so you can see the story/script/layout working end-to-end.

The PDF is: cover, a Character File page for the main character (the first character, or any
flagged `main`), then the story pages. `GET /projects/{id}/usage` (and `usage.json`) report tokens and
estimated cost per stage; `MAX_COMIC_COST_USD` caps spend and re-running resumes where it stopped.

To use a real image backend, set `IMAGE_BACKEND=openai` and `OPENAI_API_KEY` in `.env` (reference
photos are passed to the image-edit endpoint to keep characters consistent), or
`IMAGE_BACKEND=stability` with `STABILITY_API_KEY`.

## Running the pipeline without the web UI

```python
from app.models import CharacterProfile, ComicProject
from app.pipeline.orchestrator import ComicPipeline
from app.storage import new_project_id, save_project

characters = [
    CharacterProfile(
        id="char_0", name="Kaya", backstory="A retired storm-chaser turned lighthouse keeper.",
        personality="Gruff but protective", visual_description="Tall, silver undercut, weathered coat, eye patch",
    ),
]
project = ComicProject(id=new_project_id(), characters=characters, theme="a storm on the last night before the lighthouse is decommissioned")
save_project(project)

pdf_path = ComicPipeline().run_all(project)
print(pdf_path)
```

## Tests

```bash
pytest
```

Story/script generator tests mock the Anthropic client, so they run without an API key. Layout
tests exercise real Pillow rendering.

## Project layout

```
app/
  models.py               pydantic schema shared by the pipeline and the API
  config.py                env-driven settings
  storage.py                flat-file JSON persistence per project id
  pipeline/
    story_generator.py      Claude call #1: cast -> StoryArc
    script_generator.py     Claude call #2: StoryArc -> pages/panels/dialogue
    image_generator.py      ImageGenerator interface + Mock and Stability AI backends
    layout.py                Pillow-based page compositor + PDF export
    orchestrator.py          chains the four stages, persists after each
  main.py                    FastAPI app
static/                      no-build-step HTML/CSS/JS frontend
tests/
data/projects/                per-project generated artifacts (gitignored)
```
