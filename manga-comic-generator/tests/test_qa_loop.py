import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from PIL import Image

from app.models import CharacterProfile, ComicPage, ComicProject, Panel
from app.pipeline import judge as judge_module
from app.pipeline import orchestrator as orch_module
from app.pipeline.image_generator import ImageGenerator, MockImageGenerator, build_panel_prompt
from app.pipeline.judge import PanelJudge
from app.pipeline.orchestrator import ComicPipeline


def _settings(**kw):
    base = dict(require_bible_approval=False, require_sheet_approval=True, sheet_candidates=3, max_comic_cost_usd=100.0, qa_max_retries=2)
    return SimpleNamespace(**{**base, **kw})


def _setup(tmp_path, monkeypatch, **kw):
    monkeypatch.setattr(orch_module, "project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setattr(orch_module, "save_project", lambda p: None)
    monkeypatch.setattr(orch_module, "settings", _settings(**kw))
    pipeline = ComicPipeline.__new__(ComicPipeline)
    return pipeline


class FakeGen(ImageGenerator):
    def __init__(self):
        self.hints = []
        self.paths = []

    def generate_panel(self, panel, characters, output_path: Path):
        self.hints.append(panel.retry_hint)
        self.paths.append(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(output_path.name.encode())
        return output_path


class FakeJudge:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)  # list of problem-lists (or an Exception)
        self.usage_sink = None
        self.calls = []

    def review(self, path, cast, strict, detail=""):
        self.calls.append((Path(path).name, [c.name for c in cast], list(strict)))
        v = self.verdicts.pop(0)
        if isinstance(v, Exception):
            raise v
        return SimpleNamespace(problems=lambda: v)


def _project(main_panel=True):
    bruno = CharacterProfile(id="b", name="Bruno", backstory="x", main=True)
    sol = CharacterProfile(id="s", name="Sol", backstory="x")
    chars = ["Bruno"] if main_panel else ["Sol"]
    panel = Panel(panel_number=1, characters=chars, scene_description="x")
    return ComicProject(id="t", characters=[bruno, sol], pages=[ComicPage(page_number=1, panels=[panel])])


def test_failed_check_redraws_with_the_judges_reasons_and_keeps_the_pass(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([["Bruno: Nose: large oval -- Nose is small and pointed"], []])
    project = _project()
    p.generate_images(project)
    panel = project.pages[0].panels[0]
    assert panel.qa_attempts == 2 and panel.qa_passed is True and panel.qa_problems == []
    assert p.image_generator.hints[0] == "" and "Nose: Nose is small and pointed" in p.image_generator.hints[1]
    final = tmp_path / "t" / "panels" / "page1_panel1.png"
    assert final.read_bytes() == b"page1_panel1_try2.png"  # the passing attempt is the final image
    assert (tmp_path / "t" / "panels" / "rejected" / "page1_panel1_try1.png").exists()
    assert panel.retry_hint == ""  # cleared after the panel is done


def test_never_passing_keeps_best_attempt_flags_it_and_stops_at_retry_cap(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch, qa_max_retries=2)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([["a", "b", "c"], ["a"], ["a", "b"]])
    project = _project()
    p.generate_images(project)
    panel = project.pages[0].panels[0]
    assert panel.qa_attempts == 3 and panel.qa_passed is False and panel.qa_problems == ["a"]
    assert (tmp_path / "t" / "panels" / "page1_panel1.png").read_bytes() == b"page1_panel1_try2.png"  # fewest problems
    report = p.qa_report(project)
    assert report["passed"] == 0 and report["flagged_for_review"][0]["problems"] == ["a"] and report["extra_attempts"] == 2


def test_supporting_only_panels_get_a_single_retry(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch, qa_max_retries=2)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([["x"], ["x"], ["x"]])
    project = _project(main_panel=False)
    p.generate_images(project)
    assert project.pages[0].panels[0].qa_attempts == 2
    assert p.judge.calls[0][2] == []  # no strict character in this panel


def test_judge_error_accepts_the_panel_unchecked(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([requests.exceptions.ConnectionError("down")])
    project = _project()
    p.generate_images(project)
    panel = project.pages[0].panels[0]
    assert panel.qa_passed is None and panel.qa_attempts == 1 and panel.image_path
    assert (tmp_path / "t" / "panels" / "page1_panel1.png").exists()
    assert p.qa_report(project)["unchecked"] == 1


def test_no_judge_means_one_plain_attempt(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    project = _project()
    p.generate_images(project)
    assert len(p.image_generator.paths) == 1 and p.image_generator.paths[0].name == "page1_panel1.png"
    assert project.pages[0].panels[0].qa_passed is None


def test_retry_flagged_regenerates_only_failed_panels(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch, qa_max_retries=0)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([["bad"]])
    project = _project()
    p.generate_images(project)
    assert project.pages[0].panels[0].qa_passed is False
    p.generate_images(project)  # plain re-run: nothing to do
    assert len(p.image_generator.paths) == 1
    p.judge = FakeJudge([[]])
    p.generate_images(project, retry_flagged=True)
    assert len(p.image_generator.paths) == 2 and project.pages[0].panels[0].qa_passed is True


def test_retry_hint_reaches_the_image_prompt():
    panel = Panel(panel_number=1, characters=[], scene_description="x", retry_hint="Nose: too small")
    assert "CORRECTIONS" in build_panel_prompt(panel, []) and "Nose: too small" in build_panel_prompt(panel, [])
    panel.retry_hint = ""
    assert "CORRECTIONS" not in build_panel_prompt(panel, [])


def test_hint_line_shortens_judge_findings():
    line = ComicPipeline._hint_line("Bruno: Nose: a large rounded oval nose -- FAIL if small -- Nose is small and pointed")
    assert line == "Nose: Nose is small and pointed"
    assert ComicPipeline._hint_line("text drawn in the artwork") == "text drawn in the artwork"
    # the judge sometimes echoes only the feature name, without the ': description' part
    assert ComicPipeline._hint_line("Bruno: Angry-shaped eyes -- Pupils lack the crescent highlight") == "Angry-shaped eyes: Pupils lack the crescent highlight"


def test_sheet_judge_scores_candidates_and_supporting_is_auto_picked(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = MockImageGenerator()
    judge = MagicMock()
    judge.review_sheet.side_effect = [SimpleNamespace(problems=lambda: ["heart too neat", "nose small"]), SimpleNamespace(problems=lambda: ["heart too neat"]), SimpleNamespace(problems=lambda: []), ValueError("bad judge")]
    p.judge = judge
    photo = tmp_path / "photo.png"
    Image.new("RGB", (100, 100)).save(photo)
    project = _project()
    for c in project.characters:
        c.reference_image_path = str(photo)
    p.generate_character_sheets(project)
    bruno, sol = project.characters
    assert [len(c.problems) for c in bruno.sheet_candidates] == [2, 1, 0] and all(c.checked for c in bruno.sheet_candidates)
    assert sol.sheet_candidates[0].problems[0].startswith("judge error") and sol.sheet_image_path  # judge failure doesn't block


def test_gate_blocks_images_and_cover_until_main_sheet_is_picked(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    project = _project()
    p.image_generator.generate_character_sheet = lambda c, out: (out.parent.mkdir(parents=True, exist_ok=True), out.write_bytes(b"s"), out)[2]
    p.generate_character_sheets(project)
    with pytest.raises(ValueError, match="Pick a character sheet for Bruno"):
        p.generate_images(project)
    project.story = SimpleNamespace()
    with pytest.raises(ValueError, match="Pick a character sheet for Bruno"):
        p.generate_cover(project)
    p.select_sheet(project, "b", 2)
    assert project.characters[0].sheet_image_path.endswith("sheet_b_c2.png") and project.characters[0].sheet_approved
    p.generate_images(project)  # now allowed
    with pytest.raises(ValueError):
        p.select_sheet(project, "b", 9)
    p.reset_sheets(project, "b")
    assert project.characters[0].sheet_candidates == [] and not project.characters[0].sheet_approved


def test_review_sheet_sends_photo_and_sheet_with_the_sheet_prompt(tmp_path, monkeypatch):
    sheet, photo = tmp_path / "s.png", tmp_path / "p.png"
    Image.new("RGB", (300, 200)).save(sheet)
    Image.new("RGB", (300, 200)).save(photo)
    payload = {"characters": [{"name": "Bruno", "visible": True, "likeness": 4, "checks": [{"name": "Nose", "passed": False, "note": "small"}]}], "unlisted_characters": False, "text_in_art": False, "summary": ""}
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"prompt_tokens": 1000, "completion_tokens": 100}}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    judge = PanelJudge(api_key="k", model="gpt-5")
    records = []
    judge.usage_sink = records.append
    review = judge.review_sheet(sheet, CharacterProfile(id="b", name="Bruno", backstory="x", reference_image_path=str(photo)))
    body = post.call_args.kwargs["json"]
    assert "ground truth" in body["messages"][0]["content"] and post.call_count == 1  # sheet prompt, no locator call
    assert sum(1 for part in body["messages"][1]["content"] if part["type"] == "image_url") == 2
    assert not review.passed and "Bruno: Nose" in review.problems()[0] and records[0].stage == "qa"


def test_redraw_that_does_not_improve_stops_early(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch, qa_max_retries=2)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([["a", "b"], ["a", "b"]])  # second attempt no better -> no third draw
    project = _project()
    p.generate_images(project)
    assert project.pages[0].panels[0].qa_attempts == 2 and len(p.image_generator.paths) == 2


def test_minor_markers_are_advisory_and_never_fail_a_panel():
    from app.models import BibleMarker, CharacterBible
    from app.pipeline.bible import bible_checks
    from app.pipeline.judge import CharacterReview, CheckResult, PanelReview

    bruno = CharacterProfile(id="b", name="Bruno", backstory="x", bible=CharacterBible(markers=[
        BibleMarker(feature="Nose", description="big oval"), BibleMarker(feature="Eye highlight", description="crescent", critical=False)]))
    assert bible_checks(bruno)[0] == "Nose: big oval" and bible_checks(bruno)[1].startswith("MINOR: Eye highlight")
    review = PanelReview(characters=[CharacterReview(name="Bruno", visible=True, likeness=4, checks=[
        CheckResult(name="Nose: big oval", passed=True, note="ok"), CheckResult(name="MINOR: Eye highlight: crescent", passed=False, note="a dot")])],
        unlisted_characters=False, text_in_art=False, summary="", strict_names=["Bruno"])
    assert review.passed and review.problems() == [] and "a dot" in review.warnings()[0]


def test_retry_flagged_also_rechecks_panels_the_judge_could_not_check(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([requests.exceptions.ConnectionError("x")])
    project = _project()
    p.generate_images(project)
    assert project.pages[0].panels[0].qa_passed is None
    p.judge = FakeJudge([[]])
    p.generate_images(project, retry_flagged=True)
    assert project.pages[0].panels[0].qa_passed is True


def test_every_qa_attempt_is_logged_with_its_problems_and_the_hint_used(tmp_path, monkeypatch):
    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    p.judge = FakeJudge([["Bruno: Nose: large oval -- small and pointed"], []])
    p.generate_images(_project())
    events = [json.loads(line) for line in (tmp_path / "t" / "qa_events.jsonl").read_text().splitlines()]
    assert [e["attempt"] for e in events] == [1, 2] and events[0]["problem_count"] == 1 and events[1]["problem_count"] == 0
    assert events[0]["hint_used"] == "" and "small and pointed" in events[1]["hint_used"] and events[0]["strict"] is True


def test_script_is_checked_against_the_bible_before_any_image_is_drawn(tmp_path, monkeypatch):
    from app.models import CharacterBible

    p = _setup(tmp_path, monkeypatch)
    p.image_generator = FakeGen()
    project = _project()
    project.characters[0].bible = CharacterBible(forbidden_words=["fist"])
    order = []
    p.script_generator = MagicMock()
    p.script_generator.enforce_forbidden_words.side_effect = lambda pages, chars: (order.append("lint"), pages)[1]
    orig = p.image_generator.generate_panel
    p.image_generator.generate_panel = lambda *a, **k: (order.append("draw"), orig(*a, **k))[1]
    p.generate_images(project)
    assert order[0] == "lint" and "draw" in order

    # no bibles -> no lint call, no extra cost
    p.script_generator.reset_mock()
    plain = _project()
    plain.characters[0].bible = None
    p.generate_images(plain)
    p.script_generator.enforce_forbidden_words.assert_not_called()
