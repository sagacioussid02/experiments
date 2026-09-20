import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.models import BibleMarker, CharacterBible, CharacterProfile, ComicPage, ComicProject, MarkerCheck, Panel, SheetCandidate
from app.pipeline import judge as judge_module
from app.pipeline import orchestrator as orch_module
from app.pipeline.image_generator import ImageGenerator
from app.pipeline.judge import PanelJudge
from app.pipeline.orchestrator import ComicPipeline


def _bible():
    return CharacterBible(markers=[
        BibleMarker(feature="Eyes", description="almond, fierce"),
        BibleMarker(feature="Nose", description="large black oval"),
        BibleMarker(feature="Highlight", description="crescent", critical=False),
    ])


def _bruno():
    return CharacterProfile(id="b", name="Bruno", backstory="x", main=True, bible=_bible())


def _check(marker, photo=True, bible=True, shows="", critical=True):
    return MarkerCheck(marker=marker, critical=critical, matches_photo=photo, matches_bible=bible, sheet_shows=shows, note="n")


def _cand(path="s.png", markers=(), text=False):
    return SheetCandidate(path=path, markers=list(markers), text_in_art=text)


def _pipeline(tmp_path, monkeypatch, **cfg):
    monkeypatch.setattr(orch_module, "project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setattr(orch_module, "save_project", lambda p: None)
    base = dict(require_bible_approval=False, require_sheet_approval=True, sheet_candidates=3, max_comic_cost_usd=100.0, qa_max_retries=2, qa_breaker_min_panels=4, qa_breaker_pass_rate=0.5)
    monkeypatch.setattr(orch_module, "settings", SimpleNamespace(**{**base, **cfg}))
    p = ComicPipeline.__new__(ComicPipeline)
    p.script_generator = MagicMock()
    p.script_generator.enforce_forbidden_words.side_effect = lambda pages, chars: pages
    return p


def test_candidate_score_prefers_usable_then_photo_fidelity_then_bible_agreement():
    score = ComicPipeline._candidate_score
    clean = _cand(markers=[_check("Eyes"), _check("Nose")])
    bible_off = _cand(markers=[_check("Eyes", bible=False), _check("Nose")])          # matches the photo, differs from words
    photo_off = _cand(markers=[_check("Eyes", photo=False), _check("Nose")])          # differs from the real product
    labelled = _cand(markers=[_check("Eyes"), _check("Nose")], text=True)             # a word drawn on the sheet
    minor_only = _cand(markers=[_check("Eyes"), _check("Highlight", photo=False, critical=False)])
    ranked = sorted([photo_off, bible_off, labelled, clean, minor_only], key=score)
    assert ranked[0] is clean and ranked[-1] is labelled
    assert ranked.index(minor_only) < ranked.index(bible_off) < ranked.index(photo_off)  # minor < bible words < product fidelity


def test_approval_rewrites_bible_where_sheet_matches_photo_but_not_the_words(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch)
    bruno = _bruno()
    project = ComicProject(id="t", characters=[bruno])
    cand = _cand(markers=[
        _check("Eyes", photo=True, bible=False, shows="round, soft, large whites"),       # bible words were wrong -> update
        _check("Nose", photo=False, bible=True, shows="large black oval"),                # sheet deviates from the product -> flag
        _check("Highlight", photo=True, bible=True, critical=False),
    ])
    bruno.sheet_candidates = [cand]
    v0 = bruno.bible.version
    p.select_sheet(project, "b", 1)
    eyes, nose = bruno.bible.markers[0], bruno.bible.markers[1]
    assert eyes.description == "round, soft, large whites" and not eyes.deviates_from_product
    assert nose.description == "large black oval" and nose.deviates_from_product
    assert bruno.bible.version == v0 + 1 and bruno.approval == "manual" and bruno.sheet_approved
    assert bruno.confidence.bible_updates == 1 and bruno.confidence.sheet_critical_failures == 1
    assert len(bruno.reconciliation) == 1 and "almond, fierce" in bruno.reconciliation[0]


def test_preview_lists_only_the_bible_changes_approval_would_make():
    p = ComicPipeline.__new__(ComicPipeline)
    bruno = _bruno()
    bruno.sheet_candidates = [_cand(markers=[_check("Eyes", bible=False, shows="round eyes"), _check("Nose", photo=False)])]
    preview = p.reconciliation_preview(bruno, 1)
    assert len(preview) == 1 and "round eyes" in preview[0]


def test_no_bible_change_means_no_version_bump(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch)
    bruno = _bruno()
    project = ComicProject(id="t", characters=[bruno])
    bruno.sheet_candidates = [_cand(markers=[_check("Eyes"), _check("Nose")])]
    p.select_sheet(project, "b", 1)
    assert bruno.bible.version == 1 and bruno.reconciliation == []


class SheetGen(ImageGenerator):
    def generate_panel(self, panel, characters, output_path):  # pragma: no cover - not used here
        raise AssertionError

    def generate_character_sheet(self, character, output_path: Path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"s")
        return output_path


def _judge_returning(reviews):
    judge = MagicMock()
    judge.review_sheet_markers.side_effect = [SimpleNamespace(markers=r[0], text_in_art=r[1], unlisted_characters=False, summary="") for r in reviews]
    return judge


def _photo(tmp_path, c):
    photo = tmp_path / "photo.png"
    Image.new("RGB", (10, 10)).save(photo)
    c.reference_image_path = str(photo)


def test_soft_gate_auto_selects_the_best_candidate_and_reconciles(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch, require_sheet_approval=False)
    p.image_generator = SheetGen()
    bruno = _bruno()
    _photo(tmp_path, bruno)
    project = ComicProject(id="t", characters=[bruno])
    p.judge = _judge_returning([
        ([_check("Eyes", photo=False), _check("Nose")], False),                                   # differs from the product
        ([_check("Eyes", photo=True, bible=False, shows="round soft eyes"), _check("Nose")], False),  # matches product, bible words off
        ([_check("Eyes"), _check("Nose")], True),                                                 # perfect but a label is drawn on it
    ])
    p.generate_character_sheets(project)
    assert bruno.approval == "auto" and bruno.sheet_approved
    assert bruno.sheet_image_path.endswith("sheet_b_c2.png")            # best usable: matches the product
    assert bruno.bible.markers[0].description == "round soft eyes"       # bible reconciled to the sheet
    assert [c.problems != [] for c in bruno.sheet_candidates] == [True, True, True]


def test_owner_gate_still_waits_when_approval_is_required(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch, require_sheet_approval=True)
    p.image_generator = SheetGen()
    bruno = _bruno()
    _photo(tmp_path, bruno)
    p.judge = _judge_returning([([_check("Eyes")], False)] * 3)
    project = ComicProject(id="t", characters=[bruno])
    p.generate_character_sheets(project)
    assert bruno.sheet_image_path is None and len(bruno.sheet_candidates) == 3 and bruno.approval is None


def test_review_sheet_markers_sends_photo_sheet_and_maps_criticality(tmp_path, monkeypatch):
    sheet, photo = tmp_path / "s.png", tmp_path / "p.png"
    Image.new("RGB", (300, 200)).save(sheet)
    Image.new("RGB", (300, 200)).save(photo)
    bruno = _bruno()
    bruno.reference_image_path = str(photo)
    payload = {"markers": [
        {"marker": "Eyes", "matches_photo": True, "matches_bible": False, "sheet_shows": "round", "note": "n"},
        {"marker": "Highlight", "matches_photo": True, "matches_bible": True, "sheet_shows": "dot", "note": "n"},
    ], "text_in_art": True, "unlisted_characters": False, "summary": ""}
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    post = MagicMock(return_value=response)
    import requests as _requests

    monkeypatch.setattr(_requests, "post", post)
    review = PanelJudge(api_key="k").review_sheet_markers(sheet, bruno)
    body = post.call_args.kwargs["json"]
    assert body["response_format"]["json_schema"]["name"] == "sheet_marker_review"
    assert sum(1 for part in body["messages"][1]["content"] if part["type"] == "image_url") == 2
    assert review.text_in_art and [m.critical for m in review.markers] == [True, False]  # Highlight is minor in the bible
    assert not review.markers[0].matches_bible and review.markers[0].matches_photo


def _panel_project(n=8):
    bruno = _bruno()
    panels = [Panel(panel_number=i, characters=["Bruno"], scene_description="x") for i in range(1, n + 1)]
    return ComicProject(id="t", characters=[bruno], pages=[ComicPage(page_number=1, panels=panels)])


class PanelGen(ImageGenerator):
    def __init__(self):
        self.calls = 0

    def generate_panel(self, panel, characters, output_path: Path):
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        return output_path


class ScriptedJudge:
    """Fails everything until told otherwise."""
    usage_sink = None

    def __init__(self, problems=("Eyes: too round",)):
        self.problems = list(problems)
        self.calls = 0

    def review(self, path, cast, strict, detail=""):
        self.calls += 1
        return SimpleNamespace(problems=lambda: self.problems)


def test_circuit_breaker_stops_retries_but_still_finishes_the_episode(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch, require_sheet_approval=False, qa_breaker_min_panels=4, qa_max_retries=2)
    p.image_generator, p.judge = PanelGen(), ScriptedJudge()
    project = _panel_project(8)
    p.generate_images(project)
    attempts = [x.qa_attempts for x in project.pages[0].panels]
    assert all(x.image_path for x in project.pages[0].panels)               # the user always gets a full episode
    assert project.qa_breaker and project.qa_breaker["tripped_after_panels"] == 4
    assert attempts[:4] == [2, 2, 2, 2]                                      # a redraw that does not improve stops at 2
    assert attempts[4:] == [1, 1, 1, 1]                                      # after the breaker: one draw per panel
    report = p.qa_report(project)
    assert report["circuit_breaker"]["first_attempt_pass_rate"] == 0.0 and len(report["flagged_for_review"]) == 8


def test_breaker_does_not_trip_when_first_attempts_mostly_pass(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch, qa_breaker_min_panels=4)
    p.image_generator, p.judge = PanelGen(), ScriptedJudge(problems=())
    project = _panel_project(6)
    p.generate_images(project)
    assert project.qa_breaker is None and all(x.qa_attempts == 1 and x.qa_passed for x in project.pages[0].panels)


def test_confidence_is_low_only_when_sheet_differs_from_product_and_panels_fail(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch, qa_breaker_min_panels=99)
    p.image_generator, p.judge = PanelGen(), ScriptedJudge()
    project = _panel_project(4)
    project.characters[0].confidence.sheet_critical_failures = 1
    p.generate_images(project)
    conf = project.characters[0].confidence
    assert conf.first_attempt_pass_rate == 0.0 and conf.low is True

    p2 = _pipeline(tmp_path / "b", monkeypatch, qa_breaker_min_panels=99)
    p2.image_generator, p2.judge = PanelGen(), ScriptedJudge()
    good_sheet = _panel_project(4)
    p2.generate_images(good_sheet)  # sheet matches the product: not low even though panels fail
    assert good_sheet.characters[0].confidence.low is False


def test_sheets_are_judged_by_the_stronger_sheet_judge_when_there_is_one(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch, require_sheet_approval=True, sheet_candidates=1)
    p.image_generator = SheetGen()
    bruno = _bruno()
    _photo(tmp_path, bruno)
    p.judge = MagicMock()          # cheap panel judge: must NOT be used for sheets
    p.sheet_judge = _judge_returning([([_check("Eyes")], False)])
    p.generate_character_sheets(ComicProject(id="t", characters=[bruno]))
    assert p.sheet_judge.review_sheet_markers.call_count == 1 and p.judge.review_sheet_markers.call_count == 0


def test_reconciliation_never_rewrites_a_marker_into_a_negation(tmp_path, monkeypatch):
    p = _pipeline(tmp_path, monkeypatch)
    bruno = _bruno()
    project = ComicProject(id="t", characters=[bruno])
    bruno.sheet_candidates = [_cand(markers=[_check("Eyes", bible=False, shows="No distinct eyes are drawn"), _check("Nose", bible=False, shows="a smaller oval nose")])]
    p.select_sheet(project, "b", 1)
    assert bruno.bible.markers[0].description == "almond, fierce"          # negation ignored: would fail panels that draw the feature
    assert bruno.bible.markers[1].description == "a smaller oval nose"     # positive descriptions still reconcile


def test_advisory_never_items_become_minor_checks_not_failures():
    from app.pipeline.bible import bible_checks, bible_prompt_block

    c = CharacterProfile(id="b", name="Bruno", backstory="x", bible=CharacterBible(markers=[BibleMarker(feature="Nose", description="big")], never=["fangs"], never_minor=["eyebrows", "visible fists"]))
    checks = bible_checks(c)
    assert checks[-2].startswith("Nothing added: none of -- fangs") and checks[-1].startswith("MINOR: Nothing added (advisory): avoid -- eyebrows")
    assert "Avoid if possible: eyebrows; visible fists" in bible_prompt_block(c)
