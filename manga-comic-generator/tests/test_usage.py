from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import CharacterProfile, ComicPage, ComicProject, Panel
from app.pipeline import orchestrator as orch_module
from app.pipeline.image_generator import ImageGenerator
from app.pipeline.orchestrator import ComicPipeline
from app.usage import anthropic_record, openai_image_record, summarize


def test_anthropic_cost_uses_model_price_and_cache_multipliers():
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=1_000_000, cache_creation_input_tokens=0)
    record = anthropic_record("story", "claude-sonnet-5", usage)
    # 1M*$2 + 0.1M*$10 + 1M*$2*0.1
    assert record.cost_usd == pytest.approx(3.2)


def test_unknown_model_is_unpriced_and_flagged():
    record = anthropic_record("story", "some-new-model", SimpleNamespace(input_tokens=10, output_tokens=10))
    assert record.cost_usd is None
    assert summarize([record])["unpriced_calls"] == 1


def test_openai_image_cost_splits_text_image_and_output_tokens():
    usage = {"input_tokens": 1500, "output_tokens": 1056, "input_tokens_details": {"text_tokens": 500, "image_tokens": 1000}}
    record = openai_image_record("images", "gpt-image-1", usage, "panel 1")
    assert record.cost_usd == pytest.approx((500 * 5 + 1000 * 10 + 1056 * 40) / 1e6)
    assert record.images == 1


def test_openai_record_without_usage_is_unpriced():
    assert openai_image_record("images", "gpt-image-1", None).cost_usd is None


class _FakeImages(ImageGenerator):
    def __init__(self):
        self.calls = 0

    def generate_panel(self, panel, characters, output_path: Path):
        self.calls += 1
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"x")
        self.usage_sink(openai_image_record("images", "gpt-image-1", {"input_tokens": 0, "output_tokens": 250_000}))  # $10
        return output_path


def _project():
    panels = [Panel(panel_number=i, characters=[], scene_description="x") for i in range(1, 4)]
    return ComicProject(id="t1", characters=[CharacterProfile(id="c", name="K", backstory="b")], pages=[ComicPage(page_number=1, panels=panels)])


def test_cost_cap_stops_image_generation_and_rerun_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(orch_module, "project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setattr(orch_module, "save_project", lambda p: None)
    monkeypatch.setattr(orch_module, "settings", SimpleNamespace(max_comic_cost_usd=15.0, require_sheet_approval=False, qa_max_retries=2, sheet_candidates=3))
    fake = _FakeImages()
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.image_generator = fake
    project = _project()

    with pytest.raises(RuntimeError, match="Cost cap reached"):
        pipeline.generate_images(project)
    assert fake.calls == 2  # $10, $20 >= $15 -> third panel refused
    assert project.usage and summarize(project.usage)["total_cost_usd"] == 20.0

    monkeypatch.setattr(orch_module, "settings", SimpleNamespace(max_comic_cost_usd=100.0, require_sheet_approval=False, qa_max_retries=2, sheet_candidates=3))
    pipeline.generate_images(project)
    assert fake.calls == 3  # only the missing panel was generated
    assert project.status == "images_ready"


def test_call_latency_is_recorded_on_usage_records():
    from app.usage import openai_chat_record

    assert anthropic_record("story", "claude-sonnet-5", SimpleNamespace(input_tokens=1, output_tokens=1), seconds=2.5).seconds == 2.5
    assert openai_image_record("images", "gpt-image-1", {"input_tokens": 1, "output_tokens": 1}, seconds=41.0).seconds == 41.0
    assert openai_chat_record("qa", "gpt-5", {"prompt_tokens": 1, "completion_tokens": 1}, seconds=9.0).seconds == 9.0
