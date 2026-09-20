from __future__ import annotations

import shutil
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.models import CharacterBible, CharacterProfile, ComicProject
from app.pipeline.orchestrator import ComicPipeline
from app.usage import summarize
from app.storage import load_project, new_project_id, project_dir, save_project

app = FastAPI(title="Manga Comic Generator")
pipeline = ComicPipeline()


@app.post("/projects")
async def create_project(
    names: List[str] = Form(...),
    backstories: List[str] = Form(...),
    personalities: List[str] = Form(...),
    visual_descriptions: List[str] = Form(...),
    theme: Optional[str] = Form(None),
    images: List[UploadFile] = File(default=[]),
) -> ComicProject:
    project_id = new_project_id()
    directory = project_dir(project_id) / "characters"
    directory.mkdir(parents=True, exist_ok=True)

    characters = []
    for i, name in enumerate(names):
        reference_image_path = None
        if i < len(images) and images[i] is not None and images[i].filename:
            reference_image_path = str(directory / f"{i}_{images[i].filename}")
            with open(reference_image_path, "wb") as handle:
                shutil.copyfileobj(images[i].file, handle)
        characters.append(
            CharacterProfile(
                id=f"char_{i}",
                name=name,
                backstory=backstories[i],
                personality=personalities[i] if i < len(personalities) else "",
                visual_description=visual_descriptions[i] if i < len(visual_descriptions) else "",
                reference_image_path=reference_image_path,
                main=(i == 0),
            )
        )

    project = ComicProject(id=project_id, characters=characters, theme=theme)
    save_project(project)
    return project


@app.get("/projects/{project_id}")
async def get_project(project_id: str) -> ComicProject:
    try:
        return load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/run")
async def run_pipeline(project_id: str) -> ComicProject:
    project = load_project(project_id)
    pipeline.run_all(project, project.theme)
    return load_project(project_id)


@app.post("/projects/{project_id}/bibles")
async def extract_bibles(project_id: str) -> ComicProject:
    return pipeline.extract_bibles(load_project(project_id))


@app.get("/projects/{project_id}/characters/{character_id}/bible")
async def get_bible(project_id: str, character_id: str) -> CharacterBible:
    character = next((c for c in load_project(project_id).characters if c.id == character_id), None)
    if not character or not character.bible:
        raise HTTPException(status_code=404, detail="No bible for that character")
    return character.bible


@app.put("/projects/{project_id}/characters/{character_id}/bible")
async def update_bible(project_id: str, character_id: str, bible: CharacterBible) -> CharacterBible:
    """Replace a character's bible (any edit bumps the version and requires re-approval)."""
    project = load_project(project_id)
    character = next((c for c in project.characters if c.id == character_id), None)
    if not character:
        raise HTTPException(status_code=404, detail="No such character")
    previous = character.bible.version if character.bible else 0
    bible.version, bible.approved = previous + 1, False
    character.bible = bible
    save_project(project)
    return bible


@app.post("/projects/{project_id}/characters/{character_id}/bible/approve")
async def approve_bible(project_id: str, character_id: str) -> ComicProject:
    try:
        return pipeline.approve_bible(load_project(project_id), character_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/story")
async def generate_story(project_id: str) -> ComicProject:
    return pipeline.generate_story(load_project(project_id))


@app.post("/projects/{project_id}/script")
async def generate_script(project_id: str) -> ComicProject:
    return pipeline.generate_script(load_project(project_id))


@app.post("/projects/{project_id}/sheets")
async def generate_character_sheets(project_id: str) -> ComicProject:
    return pipeline.generate_character_sheets(load_project(project_id))


@app.post("/projects/{project_id}/cover")
async def generate_cover(project_id: str) -> ComicProject:
    return pipeline.generate_cover(load_project(project_id))


@app.post("/projects/{project_id}/characters/{character_id}/sheet/select")
async def select_sheet(project_id: str, character_id: str, index: int) -> ComicProject:
    """Pick candidate `index` (1-based) as the character's frozen sheet."""
    try:
        return pipeline.select_sheet(load_project(project_id), character_id, index)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/projects/{project_id}/characters/{character_id}/sheet/reset")
async def reset_sheet(project_id: str, character_id: str) -> ComicProject:
    try:
        return pipeline.reset_sheets(load_project(project_id), character_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/projects/{project_id}/qa")
async def get_qa(project_id: str) -> dict:
    return pipeline.qa_report(load_project(project_id))


@app.post("/projects/{project_id}/images")
async def generate_images(project_id: str, retry_flagged: bool = False) -> ComicProject:
    return pipeline.generate_images(load_project(project_id), retry_flagged=retry_flagged)


@app.post("/projects/{project_id}/compose")
async def compose(project_id: str) -> ComicProject:
    pipeline.compose(load_project(project_id))
    return load_project(project_id)


@app.get("/projects/{project_id}/usage")
async def get_usage(project_id: str) -> dict:
    try:
        project = load_project(project_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {**summarize(project.usage), "records": [r.model_dump() for r in project.usage]}


@app.get("/projects/{project_id}/pdf")
async def get_pdf(project_id: str) -> FileResponse:
    pdf_path = project_dir(project_id) / "comic.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail="Comic not composed yet")
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{project_id}.pdf")


@app.get("/projects/{project_id}/pages/{page_number}")
async def get_page_image(project_id: str, page_number: int) -> FileResponse:
    page_path = project_dir(project_id) / "pages" / f"page_{page_number}.png"
    if not page_path.exists():
        raise HTTPException(status_code=404, detail="Page not rendered yet")
    return FileResponse(page_path, media_type="image/png")


# Mounted last so it only catches requests the routes above didn't handle.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
