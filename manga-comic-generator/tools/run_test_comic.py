"""Live test run. Usage: python -m tools.run_test_comic [story|images]  (story = create project + story + script)."""
import json
import shutil
import sys
from pathlib import Path

from app.models import CharacterProfile, ComicProject
from app.pipeline.orchestrator import ComicPipeline
from app.storage import load_project, new_project_id, project_dir, save_project
from app.usage import summarize

MARKER = Path("data/projects/.last_test_project")


def create() -> ComicProject:
    pid = new_project_id()
    d = project_dir(pid) / "characters"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy("IMG_9964.jpg", d / "bruno.jpg")
    shutil.copy("refs/elephant.png", d / "sol.png")
    project = ComicProject(
        id=pid,
        theme="Very short comic: exactly 2 pages, a warm and funny bedtime adventure with a little heart.",
        characters=[
            CharacterProfile(
                id="char_0", name="Bruno", reference_image_path=str(d / "bruno.jpg"),
                backstory="A handmade chenille bear who guards the bedroom at night and pretends to be tough, but secretly loves his best friend.",
                personality="Grumpy, dramatic, fiercely protective, secretly soft",
                visual_description="Chunky fluffy dark-brown chenille teddy bear, big black oval nose, wide angry-looking eyes, small white heart-shaped patch on his chest",
            ),
            CharacterProfile(
                id="char_1", name="Sol", reference_image_path=str(d / "sol.png"),
                backstory="A sleepy crocheted elephant who has never once been in a hurry and calms every crisis by taking a nap.",
                personality="Laid-back, dry-humored, unbothered, kind",
                visual_description="Soft blue-grey crocheted elephant, big floppy round ears, droopy half-closed sleepy eyes, curled trunk, cream sun-shaped patch on his belly",
            ),
        ],
    )
    save_project(project)
    MARKER.write_text(pid)
    return project


def clone_for_rerun(keep_sheets=(), reference_overrides=None) -> ComicProject:
    """New project reusing the last project's characters/story/script (no Claude spend), with
    no sheets/images and Bruno flagged as the main character. Usage starts from zero."""
    old = load_project(MARKER.read_text().strip())
    pid = new_project_id()
    d = project_dir(pid) / "characters"
    d.mkdir(parents=True, exist_ok=True)
    project = old.model_copy(deep=True)
    project.id, project.usage, project.status = pid, [], "script_ready"
    for c in project.characters:
        src = Path((reference_overrides or {}).get(c.name) or c.reference_image_path)
        shutil.copy(src, d / src.name)
        old_sheet = c.sheet_image_path
        c.reference_image_path, c.sheet_image_path = str(d / src.name), None
        c.sheet_candidates, c.sheet_approved = [], False
        if c.name in keep_sheets and old_sheet:
            shutil.copy(old_sheet, d / Path(old_sheet).name)
            c.sheet_image_path = str(d / Path(old_sheet).name)
        c.main = c.name == "Bruno"
    project.cover_image_path = None
    for page in project.pages:
        for panel in page.panels:
            panel.image_path, panel.qa_attempts, panel.qa_passed, panel.qa_problems = None, 0, None, []
    save_project(project)
    MARKER.write_text(pid)
    return project


def report(project: ComicProject) -> None:
    print(json.dumps(summarize(project.usage), indent=2))


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "story"
    pipeline = ComicPipeline()
    if stage == "story":
        project = create()
        pipeline.generate_story(project)
        pipeline.generate_script(project)
        print("PROJECT", project.id, "|", project.story.title)
        for page in project.pages:
            print(f"page {page.page_number}: {len(page.panels)} panels")
            for p in page.panels:
                print(f"  [{p.panel_number}] imp={p.importance} {p.orientation} chars={p.characters} | {p.scene_description[:110]}")
                for line in p.dialogue:
                    print(f"      {line.character} ({line.bubble_type}): {line.text}")
        report(project)
    elif stage == "v3":  # keep Bruno's sheet; rebuild Sol's from the cleaned reference; new prompts
        project = clone_for_rerun(keep_sheets=("Bruno",), reference_overrides={"Sol": "refs/elephant_clean.png"})
        pipeline.generate_character_sheets(project)
        print("PROJECT", project.id)
        report(project)
    elif stage == "v4":  # new project keeping the approved bibles; draws sheet candidates and stops for your pick
        project = clone_for_rerun()
        pipeline.generate_character_sheets(project)
        print("PROJECT", project.id)
        report(project)
    elif stage == "finish":  # after you picked the main sheet: cover + panels (with QA/retries) + PDF
        project = load_project(MARKER.read_text().strip())
        pipeline.generate_cover(project)
        pipeline.generate_images(project)
        pdf = pipeline.compose(project)
        print("PDF", pdf)
        print(json.dumps(pipeline.qa_report(project), indent=1))
        report(project)
    elif stage == "bible":
        project = load_project(MARKER.read_text().strip())
        pipeline.extract_bibles(project)
        for c in project.characters:
            print("=" * 70, "\n", c.name, "(main)" if c.main else "(supporting)", "| approved:", c.bible.approved)
            print(json.dumps(c.bible.model_dump(), indent=1))
        from app.pipeline.bible import find_forbidden
        print("forbidden words used in the EXISTING script:", find_forbidden(project.pages, project.characters))
        report(project)
    elif stage == "cover":
        project = load_project(MARKER.read_text().strip())
        pipeline.generate_cover(project)
        pdf = pipeline.compose(project)
        print("PDF", pdf, "| cover art:", project.cover_image_path)
        report(project)
    elif stage == "sheets":
        project = clone_for_rerun()
        pipeline.generate_character_sheets(project)
        print("PROJECT", project.id)
        for c in project.characters:
            print(c.name, c.sheet_image_path)
        report(project)
    else:
        project = load_project(MARKER.read_text().strip())
        pipeline.generate_images(project)
        pdf = pipeline.compose(project)
        print("PDF", pdf)
        report(project)
