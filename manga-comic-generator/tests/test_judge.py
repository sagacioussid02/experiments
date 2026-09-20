import requests
import time
import json
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.models import CharacterProfile
from app.pipeline import judge as judge_module
from app.pipeline.judge import CharacterReview, CheckResult, PanelJudge, PanelReview
from app.usage import openai_chat_record


def _review(likeness=5, passed=True, unlisted=False, text=False, name="Bruno", strict=("Bruno",)):
    return PanelReview(
        characters=[CharacterReview(name=name, visible=True, likeness=likeness, checks=[CheckResult(name="Nose", passed=passed, note="ok" if passed else "too small")])],
        unlisted_characters=unlisted, text_in_art=text, summary="s", strict_names=list(strict),
    )


def test_strict_character_fails_on_any_failed_check_but_supporting_only_needs_likeness():
    assert _review().passed
    assert not _review(passed=False).passed
    assert _review(likeness=3).passed  # the vague 1-5 score only catches gross failures
    assert not _review(likeness=2).passed
    assert _review(likeness=3, passed=False, strict=()).passed  # supporting: lenient
    assert not _review(likeness=1, strict=()).passed


def test_unlisted_character_and_text_always_fail():
    assert not _review(unlisted=True).passed
    assert not _review(text=True, strict=()).passed
    assert "text drawn in the artwork" in _review(text=True).problems()


def test_chat_pricing_is_exact_not_prefix():
    assert openai_chat_record("qa", "gpt-5", {"prompt_tokens": 1_000_000, "completion_tokens": 0}).cost_usd == pytest.approx(1.25)
    assert openai_chat_record("qa", "gpt-5-2025-08-07", {"prompt_tokens": 1_000_000, "completion_tokens": 0}).cost_usd == pytest.approx(1.25)
    assert openai_chat_record("qa", "gpt-5.4", {"prompt_tokens": 10, "completion_tokens": 10}).cost_usd is None  # NOT priced as gpt-5
    # the longest name wins: mini is a fifth of the price of gpt-5, not the same
    assert openai_chat_record("qa", "gpt-5-mini", {"prompt_tokens": 1_000_000, "completion_tokens": 0}).cost_usd == pytest.approx(0.25)
    assert openai_chat_record("qa", "gpt-5-mini-2025-08-07", {"prompt_tokens": 0, "completion_tokens": 1_000_000}).cost_usd == pytest.approx(2.0)


def _chars(tmp_path):
    sheet, photo = tmp_path / "s.png", tmp_path / "p.png"
    Image.new("RGB", (300, 200)).save(sheet)
    Image.new("RGB", (300, 200)).save(photo)
    return [CharacterProfile(id="a", name="Bruno", backstory="b", sheet_image_path=str(sheet), reference_image_path=str(photo))]


def test_review_sends_sheet_photo_and_panel_and_records_usage(tmp_path, monkeypatch):
    panel = tmp_path / "panel.png"
    Image.new("RGB", (300, 300)).save(panel)
    payload = {"characters": [{"name": "Bruno", "visible": True, "likeness": 5, "checks": [{"name": "Nose", "passed": True, "note": "ok"}]}], "unlisted_characters": False, "text_in_art": False, "summary": "fine"}
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"prompt_tokens": 4000, "completion_tokens": 300}}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    judge = PanelJudge(api_key="k", model="gpt-5", reasoning_effort="low")
    records = []
    judge.usage_sink = records.append

    review = judge.review(panel, _chars(tmp_path), ["Bruno"], detail="p1")

    assert review.passed and review.strict_names == ["Bruno"]
    body = post.call_args.kwargs["json"]
    images = [part for part in body["messages"][1]["content"] if part["type"] == "image_url"]
    assert len(images) == 3  # sheet + real product photo + panel
    assert body["response_format"]["json_schema"]["strict"] is True
    assert records[-1].stage == "qa" and records[-1].cost_usd == pytest.approx((4000 * 1.25 + 300 * 10) / 1e6)  # the review, not the locator


def test_invalid_or_refused_review_raises(tmp_path, monkeypatch):
    panel = tmp_path / "panel.png"
    Image.new("RGB", (10, 10)).save(panel)
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": "not json"}}], "usage": {}}
    monkeypatch.setattr(requests, "post", MagicMock(return_value=response))
    with pytest.raises(ValueError, match="invalid review"):
        PanelJudge(api_key="k").review(panel, _chars(tmp_path), ["Bruno"])
    response.json.return_value = {"choices": [{"message": {"content": None, "refusal": "no"}, "finish_reason": "stop"}]}
    with pytest.raises(ValueError, match="no review"):
        PanelJudge(api_key="k").review(panel, _chars(tmp_path), ["Bruno"])


def test_transient_ssl_error_is_retried_then_succeeds(monkeypatch):
    import requests

    monkeypatch.setattr(time, "sleep", lambda s: None)
    ok = MagicMock()
    calls = MagicMock(side_effect=[requests.exceptions.SSLError("bad record mac"), ok])
    monkeypatch.setattr(requests, "post", calls)
    assert PanelJudge(api_key="k")._post({}) is ok
    assert calls.call_count == 2


def test_persistent_connection_error_is_raised(monkeypatch):
    import requests

    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(requests, "post", MagicMock(side_effect=requests.exceptions.ConnectionError("down")))
    with pytest.raises(requests.exceptions.ConnectionError):
        PanelJudge(api_key="k")._post({})


def test_prompt_tells_judge_expression_may_vary_and_default_checks_are_design_only(tmp_path, monkeypatch):
    from app.pipeline.judge import ALLOWED_TO_VARY, DEFAULT_CHECKS

    assert "expression" in ALLOWED_TO_VARY and "never fail a check" in ALLOWED_TO_VARY
    assert all("mouth" not in c.lower() or "no teeth" in c.lower() for c in DEFAULT_CHECKS)
    panel = tmp_path / "panel.png"
    Image.new("RGB", (10, 10)).save(panel)
    payload = {"characters": [], "unlisted_characters": False, "text_in_art": False, "summary": ""}
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {}}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    PanelJudge(api_key="k").review(panel, _chars(tmp_path), ["Bruno"])
    first_text = post.call_args.kwargs["json"]["messages"][1]["content"][0]["text"]
    assert first_text.startswith("ALLOWED TO VARY")


def _resp(payload, usage=None):
    r = MagicMock()
    r.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}], "usage": usage or {"prompt_tokens": 100, "completion_tokens": 10}}
    return r


_EMPTY_REVIEW = {"characters": [], "unlisted_characters": False, "text_in_art": False, "summary": ""}


def test_locator_returns_normalised_box_or_none(tmp_path, monkeypatch):
    panel = tmp_path / "p.png"
    Image.new("RGB", (400, 300)).save(panel)
    char = _chars(tmp_path)[0]
    monkeypatch.setattr(requests, "post", MagicMock(return_value=_resp({"visible": True, "x0": 0.1, "y0": 0.2, "x1": 0.5, "y1": 0.6})))
    judge = PanelJudge(api_key="k")
    assert judge.locate_head(panel, char) == (0.1, 0.2, 0.5, 0.6)
    assert judge.locate_head(panel, char) == (0.1, 0.2, 0.5, 0.6)  # second call served from the cache
    assert requests.post.call_count == 1
    monkeypatch.setattr(requests, "post", MagicMock(return_value=_resp({"visible": False, "x0": 0, "y0": 0, "x1": 0, "y1": 0})))
    assert PanelJudge(api_key="k").locate_head(panel, char) is None
    monkeypatch.setattr(requests, "post", MagicMock(return_value=_resp({"visible": True, "x0": 0.6, "y0": 0.2, "x1": 0.5, "y1": 0.6})))
    assert PanelJudge(api_key="k").locate_head(panel, char) is None  # inverted box rejected


def test_review_adds_face_crop_for_strict_characters_only_and_can_be_disabled(tmp_path, monkeypatch):
    panel = tmp_path / "p.png"
    Image.new("RGB", (800, 600)).save(panel)
    chars = _chars(tmp_path)
    box = {"visible": True, "x0": 0.2, "y0": 0.2, "x1": 0.6, "y1": 0.7}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        return _resp(box if json["response_format"]["json_schema"]["name"] == "head_box" else _EMPTY_REVIEW)

    post = MagicMock(side_effect=fake_post)
    monkeypatch.setattr(requests, "post", post)
    PanelJudge(api_key="k").review(panel, chars, ["Bruno"])
    review_body = post.call_args_list[-1].kwargs["json"]
    images = [p for p in review_body["messages"][1]["content"] if p["type"] == "image_url"]
    assert len(images) == 4 and post.call_count == 2  # sheet + photo + panel + crop; locator + review calls

    post.reset_mock()
    PanelJudge(api_key="k", face_crops=False).review(panel, chars, ["Bruno"])
    assert post.call_count == 1  # no locator call
    PanelJudge(api_key="k").review(panel, chars, [])  # supporting-only cast: no crop, no locator call
    assert post.call_count == 2


def test_crop_upscales_small_regions(tmp_path):
    panel = tmp_path / "p.png"
    Image.new("RGB", (1000, 1000), "white").save(panel)
    url = judge_module._crop_url(panel, (0.4, 0.4, 0.5, 0.5))
    import base64 as b64, io as _io

    img = Image.open(_io.BytesIO(b64.b64decode(url.split(",", 1)[1])))
    assert max(img.size) == 900


def test_merge_is_strictest_wins():
    a = PanelReview(characters=[CharacterReview(name="Bruno", visible=True, likeness=5, checks=[CheckResult(name="Nose", passed=True, note="ok"), CheckResult(name="Ears", passed=True, note="ok")])], unlisted_characters=False, text_in_art=False, summary="a", strict_names=["Bruno"])
    b = PanelReview(characters=[CharacterReview(name="Bruno", visible=True, likeness=3, checks=[CheckResult(name="Nose", passed=False, note="too small"), CheckResult(name="Ears", passed=True, note="ok")])], unlisted_characters=True, text_in_art=False, summary="b", strict_names=["Bruno"])
    m = judge_module.merge_reviews([a, b])
    assert not m.passed
    bruno = m.characters[0]
    assert bruno.likeness == 3 and {k.name: k.passed for k in bruno.checks} == {"Nose": False, "Ears": True}
    assert m.unlisted_characters and m.strict_names == ["Bruno"]
    assert judge_module.merge_reviews([a, a]).passed
