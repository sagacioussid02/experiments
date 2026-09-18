from __future__ import annotations

from pathlib import Path

from app.models import ComicProject
from app.pipeline.image_generator import ImageGenerator, get_image_generator
from app.pipeline.layout import render_page, render_pages_to_pdf
from app.pipeline.script_generator import ScriptGenerator
from app.pipeline.story_generator import StoryGenerator
from app.storage import project_dir, save_project


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
        self.image_generator = image_generator or get_image_generator()

    def generate_story(self, project: ComicProject, theme: str | None = None) -> ComicProject:
        project.theme = theme or project.theme
        project.story = self.story_generator.generate(project.characters, project.theme)
        project.status = "story_ready"
        save_project(project)
        return project

    def generate_script(self, project: ComicProject) -> ComicProject:
        if not project.story:
            raise ValueError("Generate the story before the script")
        project.pages = self.script_generator.generate(project.story, project.characters)
        project.status = "script_ready"
        save_project(project)
        return project

    def generate_images(self, project: ComicProject) -> ComicProject:
        images_dir = project_dir(project.id) / "panels"
        for page in project.pages:
            for panel in page.panels:
                output_path = images_dir / f"page{page.page_number}_panel{panel.panel_number}.png"
                self.image_generator.generate_panel(panel, project.characters, output_path)
                panel.image_path = str(output_path)
        project.status = "images_ready"
        save_project(project)
        return project

    def compose(self, project: ComicProject) -> Path:
        rendered = []
        pages_dir = project_dir(project.id) / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        for page in project.pages:
            panel_images = {p.panel_number: Path(p.image_path) for p in page.panels if p.image_path}
            image = render_page(page, panel_images)
            image.save(pages_dir / f"page_{page.page_number}.png")
            rendered.append(image)
        pdf_path = project_dir(project.id) / "comic.pdf"
        render_pages_to_pdf(rendered, pdf_path)
        project.status = "composed"
        save_project(project)
        return pdf_path

    def run_all(self, project: ComicProject, theme: str | None = None) -> Path:
        self.generate_story(project, theme)
        self.generate_script(project)
        self.generate_images(project)
        return self.compose(project)
