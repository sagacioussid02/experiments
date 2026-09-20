from __future__ import annotations

import json
from pathlib import Path

from app.config import settings
from app.models import ComicProject
from app.pipeline.bible import BibleExtractor
from app.pipeline.image_generator import ImageGenerator, get_image_generator
from app.pipeline.layout import (
    assign_orientations,
    render_character_page,
    render_cover_page,
    render_page,
    render_pages_to_pdf,
    render_product_page,
)
from app.pipeline.script_generator import ScriptGenerator
from app.pipeline.story_generator import StoryGenerator
from app.usage import summarize, total_cost
from app.storage import project_dir, save_project


def main_characters(project: ComicProject) -> list:
    """Characters flagged `main`, or the first character if none is flagged."""
    flagged = [c for c in project.characters if c.main]
    return flagged or project.characters[:1]


class ComicPipeline:
    """Coordinates the four generation stages.

    Each stage mutates and persists the ComicProject (status field advances
    created -> story_ready -> script_ready -> images_ready -> composed) so a crash partway
    through never loses already-paid-for work -- resuming means re-running only the stages
    after the last saved status. See DESIGN.md's "Orchestration" section.
    """

    def __init__(self, image_generator: ImageGenerator | None = None):
        self.story_generator = StoryGenerator()
        self.script_generator = ScriptGenerator()
        self.bible_extractor = BibleExtractor()
        self.image_generator = image_generator or get_image_generator()

    def extract_bibles(self, project: ComicProject) -> ComicProject:
        """Draft a design bible for every character that has a photo and no bible yet. Supporting
        characters are auto-approved (they may drift a little); the main character must be reviewed,
        edited if needed, and approved by a human before anything downstream runs."""
        self.bible_extractor.usage_sink = project.usage.append
        main_ids = {c.id for c in main_characters(project)}
        for character in project.characters:
            if character.bible or not (character.reference_image_path and Path(character.reference_image_path).exists()):
                continue
            character.bible = self.bible_extractor.extract(character, character.reference_image_path)
            character.bible.approved = character.id not in main_ids
            save_project(project)
        return project

    def approve_bible(self, project: ComicProject, character_id: str) -> ComicProject:
        character = next((c for c in project.characters if c.id == character_id), None)
        if not character or not character.bible:
            raise ValueError(f"No bible to approve for character {character_id}")
        character.bible.approved = True
        save_project(project)
        return project

    def _require_approved_bibles(self, project: ComicProject) -> None:
        if not settings.require_bible_approval:
            return
        pending = [c.name for c in main_characters(project) if not (c.bible and c.bible.approved)]
        if pending:
            raise ValueError(
                f"Approve the design bible for {', '.join(pending)} first (extract it, review/edit it, then approve)."
            )

    def generate_story(self, project: ComicProject, theme: str | None = None) -> ComicProject:
        self.story_generator.usage_sink = project.usage.append
        project.theme = theme or project.theme
        project.story = self.story_generator.generate(project.characters, project.theme)
        project.status = "story_ready"
        save_project(project)
        return project

    def generate_script(self, project: ComicProject) -> ComicProject:
        if not project.story:
            raise ValueError("Generate the story before the script")
        self._require_approved_bibles(project)
        self.script_generator.usage_sink = project.usage.append
        project.pages = self.script_generator.generate(project.story, project.characters)
        for page in project.pages:
            assign_orientations(page)
        project.status = "script_ready"
        save_project(project)
        return project

    def _check_budget(self, project: ComicProject, stage: str) -> None:
        spent = total_cost(project.usage)
        if spent >= settings.max_comic_cost_usd:
            save_project(project)
            raise RuntimeError(
                f"Cost cap reached (${spent:.2f} >= MAX_COMIC_COST_USD=${settings.max_comic_cost_usd:.2f}); "
                f"raise the cap and re-run the {stage} stage to resume."
            )

    def generate_character_sheets(self, project: ComicProject) -> ComicProject:
        """One clean manga-style reference sheet per character, made from their photo. Panels use
        these instead of the raw photos (already in the target style, single subject, no clutter).
        Existing sheets are kept, so this is safe to re-run."""
        self._require_approved_bibles(project)
        self.image_generator.usage_sink = project.usage.append
        sheets_dir = project_dir(project.id) / "characters"
        for character in project.characters:
            if character.sheet_image_path and Path(character.sheet_image_path).exists():
                continue
            self._check_budget(project, "sheets")
            output = sheets_dir / f"sheet_{character.id}.png"
            result = self.image_generator.generate_character_sheet(character, output)
            if result:
                character.sheet_image_path = str(result)
                save_project(project)
        project.status = "sheets_ready"
        save_project(project)
        return project

    def generate_cover(self, project: ComicProject) -> ComicProject:
        """Cover art from the story + character sheets. Kept if it already exists; if the backend
        can't make one, compose() falls back to a character sheet."""
        if not project.story:
            raise ValueError("Generate the story before the cover")
        if project.cover_image_path and Path(project.cover_image_path).exists():
            return project
        self.image_generator.usage_sink = project.usage.append
        self._check_budget(project, "cover")
        result = self.image_generator.generate_cover(project.story, project.characters, project_dir(project.id) / "cover_art.png")
        if result:
            project.cover_image_path = str(result)
        save_project(project)
        return project

    def generate_images(self, project: ComicProject) -> ComicProject:
        """Generates any panel that has no image yet, saving after each one so a crash or the
        cost cap never loses paid-for panels (re-running resumes where it stopped)."""
        self.image_generator.usage_sink = project.usage.append
        images_dir = project_dir(project.id) / "panels"
        for page in project.pages:
            for panel in page.panels:
                if panel.image_path and Path(panel.image_path).exists():
                    continue
                self._check_budget(project, "images")
                output_path = images_dir / f"page{page.page_number}_panel{panel.panel_number}.png"
                self.image_generator.generate_panel(panel, project.characters, output_path)
                panel.image_path = str(output_path)
                save_project(project)
        project.status = "images_ready"
        save_project(project)
        return project

    def compose(self, project: ComicProject) -> Path:
        rendered = []
        pages_dir = project_dir(project.id) / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        if project.story:
            lead = main_characters(project)
            cover = render_cover_page(
                project.story.title,
                project.story.logline,
                project.cover_image_path,
                [c.name for c in project.characters],
                fallback_sheet=(lead[0].sheet_image_path or lead[0].reference_image_path) if lead else None,
            )
            cover.save(pages_dir / "cover.png")
            rendered.append(cover)
        for character in main_characters(project):
            profile = render_character_page(character, character.sheet_image_path or character.reference_image_path)
            profile.save(pages_dir / f"character_{character.id}.png")
            rendered.append(profile)
        for page in project.pages:
            panel_images = {p.panel_number: Path(p.image_path) for p in page.panels if p.image_path}
            image = render_page(page, panel_images)
            image.save(pages_dir / f"page_{page.page_number}.png")
            rendered.append(image)
        for character in main_characters(project):
            if character.reference_image_path and Path(character.reference_image_path).exists():
                back = render_product_page(character, character.reference_image_path)
                back.save(pages_dir / f"product_{character.id}.png")
                rendered.append(back)
        pdf_path = project_dir(project.id) / "comic.pdf"
        render_pages_to_pdf(rendered, pdf_path)
        (project_dir(project.id) / "usage.json").write_text(json.dumps(summarize(project.usage), indent=2))
        project.status = "composed"
        save_project(project)
        return pdf_path

    def run_all(self, project: ComicProject, theme: str | None = None) -> Path:
        self.extract_bibles(project)
        self.generate_story(project, theme)
        self.generate_script(project)
        self.generate_character_sheets(project)
        self.generate_cover(project)
        self.generate_images(project)
        return self.compose(project)
