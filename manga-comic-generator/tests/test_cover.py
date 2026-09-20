import base64
from unittest.mock import MagicMock

from PIL import Image

from app.models import CharacterProfile, ComicProject, StoryArc
from app.pipeline import image_generator as ig
from app.pipeline import orchestrator as orch_module
from app.pipeline.image_generator import MockImageGenerator, OpenAIImageGenerator, build_cover_prompt
from app.pipeline.layout import PAGE_SIZE, render_cover_page
from app.pipeline.orchestrator import ComicPipeline

STORY = StoryArc(title="Bruno vs. The Closet Monster", genre="comedy", logline="A grumpy bear fights a shadow.", synopsis="s", chapters=["a"])


def _chars(tmp_path):
    sheet = tmp_path / "s.png"
    Image.new("RGB", (1536, 1024), "gray").save(sheet)
    return [CharacterProfile(id="a", name="Bruno", backstory="b", visual_description="brown bear", sheet_image_path=str(sheet))]


def test_cover_renders_with_art_fallback_sheet_and_nothing(tmp_path):
    art = tmp_path / "art.png"
    Image.new("RGB", (1024, 1536), (80, 90, 100)).save(art)
    sheet = _chars(tmp_path)[0].sheet_image_path
    assert render_cover_page("Title", "Tag", art, ["Bruno"]).size == PAGE_SIZE
    assert render_cover_page("Title", "Tag", None, ["Bruno"], fallback_sheet=sheet).size == PAGE_SIZE
    assert render_cover_page("Title", "", None, []).size == PAGE_SIZE


def test_cover_survives_very_long_title_and_tagline():
    image = render_cover_page("An Extremely Long Title " * 8, "tagline words " * 60, None, ["A", "B", "C"])
    assert image.size == PAGE_SIZE


def test_cover_prompt_omits_title_and_bans_text(tmp_path):
    prompt = build_cover_prompt(STORY, _chars(tmp_path))
    assert "Closet Monster" not in prompt  # the title must not reach the image model
    assert "A grumpy bear fights a shadow." in prompt
    assert "Draw ONLY these characters" in prompt and "Do NOT draw any text" in prompt


def test_openai_cover_uses_portrait_edit_with_sheets_and_records_usage(tmp_path, monkeypatch):
    response = MagicMock()
    response.json.return_value = {"data": [{"b64_json": base64.b64encode(b"img").decode()}], "usage": {"input_tokens": 10, "output_tokens": 1584}}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(ig.requests, "post", post)
    gen = OpenAIImageGenerator(api_key="k")
    records = []
    gen.usage_sink = records.append
    out = gen.generate_cover(STORY, _chars(tmp_path), tmp_path / "cover.png")
    assert out.read_bytes() == b"img"
    assert post.call_args.args[0].endswith("/images/edits")
    assert post.call_args.kwargs["data"]["size"] == "1024x1536"
    assert records[0].stage == "cover"


def _pipeline(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_module, "project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setattr(orch_module, "save_project", lambda p: None)
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.image_generator = MockImageGenerator()
    return pipeline


def test_cover_stage_requires_story_and_mock_backend_falls_back(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch)
    project = ComicProject(id="t", characters=_chars(tmp_path))
    try:
        pipeline.generate_cover(project)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    project.story = STORY
    pipeline.generate_cover(project)
    assert project.cover_image_path is None  # mock can't draw covers
    pipeline.compose(project)
    assert (tmp_path / "t" / "pages" / "cover.png").exists()


def test_cover_stage_skips_existing_art(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch)
    art = tmp_path / "art.png"
    Image.new("RGB", (10, 10)).save(art)
    project = ComicProject(id="t", characters=_chars(tmp_path), story=STORY, cover_image_path=str(art))
    calls = []
    pipeline.image_generator.generate_cover = lambda *a, **k: calls.append(1)
    pipeline.generate_cover(project)
    assert calls == []


def test_tagline_shrinks_to_fit_and_only_truncates_with_ellipsis():
    from app.pipeline.layout import _fit_tagline

    lines, _ = _fit_tagline("A self-appointed toy bear guard must face down a terrifying closet monster that turns out to be a shoe, with help from his unbothered best friend.", 2600, None)
    assert len(lines) <= 3 and "friend." in lines[-1]  # nothing lost
    lines, _ = _fit_tagline("word " * 300, 2600, None)
    assert len(lines) == 3 and lines[-1].endswith("…")
