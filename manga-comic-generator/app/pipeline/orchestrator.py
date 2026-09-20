from __future__ import annotations

import json
import shutil
from pathlib import Path

import requests

from app.config import settings
from app.models import ComicProject, SheetCandidate
from app.pipeline.bible import BibleExtractor
from app.pipeline.image_generator import ImageGenerator, get_image_generator
from app.pipeline.judge import PanelJudge
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

    judge: PanelJudge | None = None

    def __init__(self, image_generator: ImageGenerator | None = None, judge: PanelJudge | None = None):
        self.story_generator = StoryGenerator()
        self.script_generator = ScriptGenerator()
        self.bible_extractor = BibleExtractor()
        self.image_generator = image_generator or get_image_generator()
        # The judge only makes sense for a real image backend that has an OpenAI key.
        if judge is None and settings.panel_qa and settings.image_backend == "openai" and settings.openai_api_key:
            judge = PanelJudge()
        self.judge = judge

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

    def _require_sheets_picked(self, project: ComicProject) -> None:
        if not settings.require_sheet_approval:
            return
        pending = [c.name for c in main_characters(project) if c.sheet_candidates and not c.sheet_approved]
        if pending:
            raise ValueError(f"Pick a character sheet for {', '.join(pending)} first (see the candidates, then select one).")

    def generate_character_sheets(self, project: ComicProject) -> ComicProject:
        """Character sheets made from the product photo. The main character gets several candidates,
        each checked by the judge against the real photo, and a human picks one (it is then frozen and
        reused); supporting characters get one, auto-selected. Existing sheets are kept, so re-running
        is safe."""
        self._require_approved_bibles(project)
        self.image_generator.usage_sink = project.usage.append
        if self.judge:
            self.judge.usage_sink = project.usage.append
        sheets_dir = project_dir(project.id) / "characters"
        main_ids = {c.id for c in main_characters(project)}
        for character in project.characters:
            if character.sheet_image_path and Path(character.sheet_image_path).exists():
                continue
            if character.id in main_ids and character.sheet_candidates and all(Path(c.path).exists() for c in character.sheet_candidates):
                continue  # candidates exist; waiting for a human pick
            character.sheet_candidates = []
            for i in range(settings.sheet_candidates if character.id in main_ids else 1):
                self._check_budget(project, "sheets")
                result = self.image_generator.generate_character_sheet(character, sheets_dir / f"sheet_{character.id}_c{i + 1}.png")
                if not result:
                    break  # this backend can't make sheets; panels fall back to the photo
                candidate = SheetCandidate(path=str(result))
                if self.judge and character.reference_image_path and Path(character.reference_image_path).exists():
                    try:
                        candidate.problems = self.judge.review_sheet(Path(result), character).problems()
                        candidate.checked = True
                    except (ValueError, requests.RequestException) as exc:
                        candidate.problems = [f"judge error: {exc}"]
                character.sheet_candidates.append(candidate)
                save_project(project)
            if character.id not in main_ids and character.sheet_candidates:
                best = min(character.sheet_candidates, key=lambda c: len(c.problems))
                character.sheet_image_path, character.sheet_approved = best.path, True
        project.status = "sheets_ready"
        save_project(project)
        return project

    def select_sheet(self, project: ComicProject, character_id: str, index: int) -> ComicProject:
        """Freeze candidate `index` (1-based) as the character's approved sheet."""
        character = next((c for c in project.characters if c.id == character_id), None)
        if not character or not (1 <= index <= len(character.sheet_candidates)):
            raise ValueError(f"No candidate {index} for character {character_id}")
        character.sheet_image_path = character.sheet_candidates[index - 1].path
        character.sheet_approved = True
        save_project(project)
        return project

    def reset_sheets(self, project: ComicProject, character_id: str) -> ComicProject:
        """Throw away a character's sheet and candidates so the next run draws new ones."""
        character = next((c for c in project.characters if c.id == character_id), None)
        if not character:
            raise ValueError(f"No such character {character_id}")
        character.sheet_image_path, character.sheet_candidates, character.sheet_approved = None, [], False
        save_project(project)
        return project

    def generate_cover(self, project: ComicProject) -> ComicProject:
        """Cover art from the story + character sheets. Kept if it already exists; if the backend
        can't make one, compose() falls back to a character sheet."""
        if not project.story:
            raise ValueError("Generate the story before the cover")
        if project.cover_image_path and Path(project.cover_image_path).exists():
            return project
        self._require_sheets_picked(project)
        self.image_generator.usage_sink = project.usage.append
        self._check_budget(project, "cover")
        result = self.image_generator.generate_cover(project.story, project.characters, project_dir(project.id) / "cover_art.png")
        if result:
            project.cover_image_path = str(result)
        save_project(project)
        return project

    @staticmethod
    def _hint_line(problem: str) -> str:
        """'Bruno: Nose: <check text> -- <judge note>' -> 'Nose: <judge note>' (short, for the next
        prompt and for grouping failures by feature in the QA log)."""
        head, sep, note = problem.rpartition(" -- ")
        if not sep:
            return problem[:230]
        head = head.split(": ", 1)[1] if ": " in head else head  # drop the leading character name
        return f"{head.split(': ', 1)[0]}: {note}"[:230]

    @staticmethod
    def _log_event(project: ComicProject, event: dict) -> None:
        """Append-only log of every QA attempt (one JSON object per line): the raw material for
        finding which checks fail most, which retries help, and what the redraws cost."""
        directory = project_dir(project.id)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / "qa_events.jsonl").open("a") as handle:
            handle.write(json.dumps(event) + "\n")

    def _draw_panel(self, project: ComicProject, panel, output_path: Path) -> None:
        """Draw one panel; if a judge is available, check it and redraw with the judge's reasons (up to
        QA_MAX_RETRIES extra tries for panels with the main character, 1 otherwise). The best attempt wins;
        a panel that never passes is kept but flagged (qa_passed False) for human review."""
        by_name = {c.name: c for c in project.characters}
        cast = [by_name[n] for n in panel.characters if n in by_name]
        main_names = {c.name for c in main_characters(project)}
        strict = [c.name for c in cast if c.name in main_names]
        if not self.judge:
            self._check_budget(project, "images")
            self.image_generator.generate_panel(panel, project.characters, output_path)
            return
        self.judge.usage_sink = project.usage.append
        retries = settings.qa_max_retries if strict else 1
        best = None  # (problem_count, path, problems)
        panel.retry_hint = ""
        attempts = 0
        for attempt in range(retries + 1):
            self._check_budget(project, "images")
            path = output_path.with_name(f"{output_path.stem}_try{attempt + 1}{output_path.suffix}")
            self.image_generator.generate_panel(panel, project.characters, path)
            attempts += 1
            try:
                problems = self.judge.review(path, cast, strict, detail=output_path.stem).problems()
            except (ValueError, requests.RequestException) as exc:
                best = (0, path, [f"judge error, not checked: {exc}"])  # accept unchecked rather than fail the run
                panel.qa_passed = None
                break
            self._log_event(project, {
                "panel": output_path.stem, "attempt": attempt + 1, "strict": bool(strict),
                "problem_count": len(problems), "problems": [self._hint_line(x) for x in problems],
                "warnings": [], "hint_used": panel.retry_hint,
            })
            previous_best = best
            if best is None or len(problems) < best[0]:
                best = (len(problems), path, problems)
            if not problems:
                break
            if previous_best is not None and len(problems) >= previous_best[0]:
                break  # the redraw did not improve on the best so far: stop spending on this panel
            panel.retry_hint = "; ".join(self._hint_line(p) for p in problems[:4])
        _, chosen, problems = best
        rejected = output_path.parent / "rejected"
        rejected.mkdir(exist_ok=True)
        for attempt in range(attempts):
            candidate = output_path.with_name(f"{output_path.stem}_try{attempt + 1}{output_path.suffix}")
            if candidate == chosen:
                shutil.copyfile(candidate, output_path)
                candidate.unlink()
            elif candidate.exists():
                shutil.move(str(candidate), rejected / candidate.name)
        panel.qa_attempts = attempts
        panel.qa_problems = problems
        if not (problems and problems[0].startswith("judge error")):
            panel.qa_passed = not problems
        panel.retry_hint = ""

    def generate_images(self, project: ComicProject, retry_flagged: bool = False) -> ComicProject:
        """Generates any panel that has no image yet (and, with retry_flagged, panels that failed QA),
        saving after each one so a crash or the cost cap never loses paid-for panels."""
        self._require_sheets_picked(project)
        if any(c.bible for c in project.characters):
            # The script may predate the bible (or a bible edit): fix wording that the bible forbids
            # BEFORE paying for images that the judge would then fail (found in the run4 post-mortem).
            self.script_generator.usage_sink = project.usage.append
            project.pages = self.script_generator.enforce_forbidden_words(project.pages, project.characters)
            save_project(project)
        self.image_generator.usage_sink = project.usage.append
        images_dir = project_dir(project.id) / "panels"
        for page in project.pages:
            for panel in page.panels:
                have = panel.image_path and Path(panel.image_path).exists()
                redo = retry_flagged and (panel.qa_passed is False or (panel.qa_passed is None and panel.qa_attempts > 0))
                if have and not redo:
                    continue
                output_path = images_dir / f"page{page.page_number}_panel{panel.panel_number}.png"
                self._draw_panel(project, panel, output_path)
                panel.image_path = str(output_path)
                save_project(project)
        project.status = "images_ready"
        save_project(project)
        return project

    def qa_report(self, project: ComicProject) -> dict:
        panels = [(pg.page_number, p) for pg in project.pages for p in pg.panels]
        flagged = [{"page": pg, "panel": p.panel_number, "attempts": p.qa_attempts, "problems": p.qa_problems} for pg, p in panels if p.qa_passed is False]
        return {
            "panels": len(panels),
            "passed": sum(1 for _, p in panels if p.qa_passed),
            "flagged_for_review": flagged,
            "unchecked": sum(1 for _, p in panels if p.qa_passed is None),
            "extra_attempts": sum(max(0, p.qa_attempts - 1) for _, p in panels),
        }

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
        (project_dir(project.id) / "qa_report.json").write_text(json.dumps(self.qa_report(project), indent=2))
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
