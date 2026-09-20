import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image

from app.models import CharacterProfile, ComicProject, Panel
from app.pipeline import orchestrator as orch_module
from app.pipeline import image_generator as ig
from app.pipeline.image_generator import MockImageGenerator, OpenAIImageGenerator
from app.pipeline.layout import PAGE_SIZE, render_character_page
from app.pipeline.orchestrator import ComicPipeline, main_characters


def _char(name="Bruno", **kw):
    return CharacterProfile(id=kw.pop("id", "c0"), name=name, backstory="A handmade bear who guards the bedroom.", personality="Grumpy but soft", **kw)


def test_character_page_renders_with_and_without_sheet(tmp_path):
    sheet = tmp_path / "s.png"
    Image.new("RGB", (1536, 1024), (90, 90, 90)).save(sheet)
    assert render_character_page(_char(), sheet).size == PAGE_SIZE
    assert render_character_page(_char(), None).size == PAGE_SIZE


def test_character_page_survives_very_long_text():
    long = _char()
    long.backstory = "word " * 400
    assert render_character_page(long, None).size == PAGE_SIZE


def test_main_character_defaults_to_first_unless_flagged():
    a, b = _char("A", id="a"), _char("B", id="b")
    assert main_characters(ComicProject(id="p", characters=[a, b])) == [a]
    b.main = True
    assert main_characters(ComicProject(id="p", characters=[a, b])) == [b]


def _ok_response():
    r = MagicMock()
    r.json.return_value = {"data": [{"b64_json": base64.b64encode(b"img").decode()}], "usage": {"input_tokens": 100, "output_tokens": 1000}}
    return r


def test_openai_sheet_uses_edits_landscape_high_fidelity_and_records_sheets_usage(tmp_path, monkeypatch):
    photo = tmp_path / "p.png"
    Image.new("RGB", (800, 800), "brown").save(photo)
    post = MagicMock(return_value=_ok_response())
    monkeypatch.setattr(ig.requests, "post", post)
    gen = OpenAIImageGenerator(api_key="k")
    records = []
    gen.usage_sink = records.append
    out = gen.generate_character_sheet(_char(reference_image_path=str(photo)), tmp_path / "sheet.png")
    assert out.read_bytes() == b"img"
    assert post.call_args.args[0].endswith("/images/edits")
    assert post.call_args.kwargs["data"]["size"] == "1536x1024"
    assert post.call_args.kwargs["data"]["input_fidelity"] == ig.settings.openai_sheet_fidelity
    assert records[0].stage == "sheets" and records[0].detail == "Bruno"


def test_panel_references_prefer_sheet_over_photo(tmp_path):
    photo, sheet = tmp_path / "p.png", tmp_path / "s.png"
    Image.new("RGB", (10, 10)).save(photo)
    Image.new("RGB", (10, 10)).save(sheet)
    gen = OpenAIImageGenerator(api_key="k")
    with_sheet = _char(reference_image_path=str(photo), sheet_image_path=str(sheet))
    panel = Panel(panel_number=1, characters=["Bruno"], scene_description="x")
    assert gen._references(panel, [with_sheet]) == [sheet]
    with_sheet.sheet_image_path = None
    assert gen._references(panel, [with_sheet]) == [photo]


def test_sheet_stage_gives_main_candidates_supporting_one_and_skips_existing(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_module, "project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setattr(orch_module, "save_project", lambda p: None)
    monkeypatch.setattr(orch_module, "settings", SimpleNamespace(require_bible_approval=False, require_sheet_approval=True, sheet_candidates=3, max_comic_cost_usd=100.0, qa_max_retries=2))
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.image_generator = MockImageGenerator()
    main, side = _char("A", id="a"), _char("B", id="b")
    project = ComicProject(id="t", characters=[main, side])

    pipeline.generate_character_sheets(project)
    assert len(main.sheet_candidates) == 3 and main.sheet_image_path is None and not main.sheet_approved  # waits for a human
    assert len(side.sheet_candidates) == 1 and side.sheet_image_path and side.sheet_approved  # auto-selected
    assert project.status == "sheets_ready"

    calls = []
    pipeline.image_generator.generate_character_sheet = lambda *a, **k: calls.append(1)
    pipeline.generate_character_sheets(project)
    assert calls == []  # nothing regenerated
