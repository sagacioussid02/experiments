import requests
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PIL import Image

from app.models import BibleMarker, CharacterBible, CharacterProfile, ComicPage, ComicProject, Panel, StoryArc
from app.pipeline import orchestrator as orch_module
from app.pipeline.bible import BibleExtractor, bible_checks, bible_prompt_block, find_forbidden, script_rules
from app.pipeline.image_generator import build_panel_prompt, build_sheet_prompt
from app.pipeline.judge import DEFAULT_CHECKS, PanelJudge
from app.pipeline.layout import PAGE_SIZE, render_product_page
from app.pipeline.orchestrator import ComicPipeline
from app.pipeline.script_generator import ScriptGenerator


def _bible(approved=False):
    return CharacterBible(
        summary="A brown chenille bear.",
        markers=[BibleMarker(feature="Nose", description="large black oval, a third of the muzzle width"), BibleMarker(feature="Chest patch", description="white heart, on the character's left")],
        expression_notes="Shouting is a plain open oval mouth with no teeth.",
        never=["teeth or fangs", "claws"],
        forbidden_words=["fang", "tooth", "teeth", "claw"],
        approved=approved,
    )


def _bruno(**kw):
    return CharacterProfile(id="b", name="Bruno", backstory="x", visual_description="brown bear", bible=kw.pop("bible", _bible()), **kw)


def _panel(text, chars=("Bruno",), caption=None, n=1):
    return Panel(panel_number=n, characters=list(chars), scene_description=text, caption=caption)


def test_prompt_block_and_checks_come_from_the_bible_and_fall_back_without_one():
    block = bible_prompt_block(_bruno())
    assert "Nose: large black oval" in block and "NEVER has: teeth or fangs; claws" in block and "no teeth" in block
    checks = bible_checks(_bruno())
    assert checks[0].startswith("Nose:") and checks[-1].startswith("Nothing added")
    plain = CharacterProfile(id="p", name="P", backstory="x")
    assert bible_prompt_block(plain) == "" and bible_checks(plain) is None


def test_bible_flows_into_panel_and_sheet_prompts():
    panel = _panel("Bruno stands.")
    assert "design rules" in build_panel_prompt(panel, [_bruno()])
    assert "large black oval" in build_sheet_prompt(_bruno())
    assert "looks like: brown bear" in build_panel_prompt(panel, [CharacterProfile(id="b", name="Bruno", backstory="x", visual_description="brown bear")])


def test_judge_uses_bible_checklist_by_default(tmp_path, monkeypatch):
    import json
    from app.pipeline import judge as judge_module

    panel = tmp_path / "p.png"
    Image.new("RGB", (10, 10)).save(panel)
    response = MagicMock()
    response.json.return_value = {"choices": [{"message": {"content": json.dumps({"characters": [], "unlisted_characters": False, "text_in_art": False, "summary": ""})}}], "usage": {}}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(requests, "post", post)
    PanelJudge(api_key="k", face_crops=False).review(panel, [_bruno()], ["Bruno"])
    text = " ".join(p["text"] for p in post.call_args.kwargs["json"]["messages"][1]["content"] if p["type"] == "text")
    assert "large black oval" in text and DEFAULT_CHECKS[0] not in text


def test_find_forbidden_matches_inflections_whole_words_and_only_present_characters():
    sol = CharacterProfile(id="s", name="Sol", backstory="x", bible=CharacterBible(forbidden_words=["trunk"]))
    pages = [ComicPage(page_number=1, panels=[
        _panel("Bruno growls, tiny fangs visible.", n=1),
        _panel("A detail of the tailor's shop.", n=2),  # 'tail' not forbidden here; ensure no false hit on words
        _panel("Sol raises his trunk.", chars=("Bruno",), n=3),  # Sol not in this panel -> ignored
        _panel("Bruno bares his teeth", caption="A claw of moonlight", n=4),
    ])]
    hits = find_forbidden(pages, [_bruno(), sol])
    assert (1, 1, "fang") in hits and (1, 4, "teeth") in hits and (1, 4, "claw") in hits
    assert not any(h[1] in (2, 3) for h in hits)


def test_script_rules_list_expression_and_forbidden_words():
    rules = script_rules([_bruno()])
    assert "Bruno" in rules and "NEVER use these words" in rules and "fang" in rules


def _script_client(first_text, fix_text):
    def tool(name, payload):
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=payload)], usage=SimpleNamespace(input_tokens=10, output_tokens=10))

    first = tool("emit_script", {"pages": [{"page_number": 1, "panels": [{"panel_number": 1, "characters": ["Bruno"], "scene_description": first_text, "camera_angle": "close-up"}]}]})
    fix = tool("emit_fixes", {"fixes": [{"page_number": 1, "panel_number": 1, "scene_description": fix_text}]})
    client = MagicMock()
    client.messages.create.side_effect = [first, fix]
    return client


def test_script_with_forbidden_word_is_rewritten_once(tmp_path):
    client = _script_client("Bruno growls, tiny fangs visible.", "Bruno growls, brows low, eyes narrowed.")
    gen = ScriptGenerator(client=client, model="claude-sonnet-5")
    records = []
    gen.usage_sink = records.append
    story = StoryArc(title="t", genre="g", logline="l", synopsis="s", chapters=["a"])
    pages = gen.generate(story, [_bruno()])
    assert "fang" not in pages[0].panels[0].scene_description and client.messages.create.call_count == 2
    assert len(records) == 2


def test_script_that_keeps_forbidden_word_after_rewrite_raises():
    client = _script_client("Bruno shows fangs.", "Bruno bares his fangs anyway.")
    gen = ScriptGenerator(client=client, model="claude-sonnet-5")
    story = StoryArc(title="t", genre="g", logline="l", synopsis="s", chapters=["a"])
    with pytest.raises(ValueError, match="forbidden words"):
        gen.generate(story, [_bruno()])


def test_extractor_returns_validated_bible_and_records_usage(tmp_path):
    photo = tmp_path / "p.png"
    Image.new("RGB", (2000, 1500), "brown").save(photo)
    payload = {"summary": "A bear.", "markers": [{"feature": "Nose", "description": "large oval"}], "colours": {"body": "brown"}, "expression_notes": "n", "never": ["fangs"], "forbidden_words": ["fang"]}
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(content=[SimpleNamespace(type="tool_use", input=payload)], usage=SimpleNamespace(input_tokens=1000, output_tokens=500))
    extractor = BibleExtractor(client=client, model="claude-sonnet-5")
    records = []
    extractor.usage_sink = records.append
    bible = extractor.extract(CharacterProfile(id="b", name="Bruno", backstory="x"), str(photo))
    assert bible.markers[0].feature == "Nose" and bible.approved is False and bible.version == 1
    payload["summary"] = "  "
    blank = extractor.extract(CharacterProfile(id="b", name="Bruno", backstory="x", visual_description="brown bear"), str(photo))
    assert blank.summary == "brown bear"  # blank summary falls back to the owner's description
    sent = client.messages.create.call_args.kwargs
    assert sent["tool_choice"]["name"] == "emit_character_bible" and sent["messages"][0]["content"][0]["type"] == "image"
    assert records[0].stage == "bible"


def _pipeline(tmp_path, monkeypatch, extractor=None):
    monkeypatch.setattr(orch_module, "project_dir", lambda pid: tmp_path / pid)
    monkeypatch.setattr(orch_module, "save_project", lambda p: None)
    monkeypatch.setattr(orch_module, "settings", SimpleNamespace(require_bible_approval=True, max_comic_cost_usd=100.0))
    pipeline = ComicPipeline.__new__(ComicPipeline)
    pipeline.bible_extractor = extractor or MagicMock()
    pipeline.image_generator = MagicMock()
    return pipeline


def test_main_bible_must_be_approved_before_script_and_sheets_supporting_is_auto_approved(tmp_path, monkeypatch):
    photo = tmp_path / "p.png"
    Image.new("RGB", (50, 50)).save(photo)
    extractor = MagicMock()
    extractor.extract.side_effect = lambda c, path: _bible()
    pipeline = _pipeline(tmp_path, monkeypatch, extractor)
    bruno = CharacterProfile(id="b", name="Bruno", backstory="x", main=True, reference_image_path=str(photo))
    sol = CharacterProfile(id="s", name="Sol", backstory="x", reference_image_path=str(photo))
    project = ComicProject(id="t", characters=[bruno, sol], story=StoryArc(title="t", genre="g", logline="l", synopsis="s", chapters=["a"]))

    pipeline.extract_bibles(project)
    assert bruno.bible and not bruno.bible.approved
    assert sol.bible and sol.bible.approved
    with pytest.raises(ValueError, match="Approve the design bible for Bruno"):
        pipeline.generate_script(project)
    with pytest.raises(ValueError, match="Approve the design bible for Bruno"):
        pipeline.generate_character_sheets(project)

    pipeline.approve_bible(project, "b")
    assert bruno.bible.approved
    pipeline.extract_bibles(project)  # idempotent: existing bibles are kept
    assert extractor.extract.call_count == 2


def test_approving_unknown_character_or_missing_bible_raises(tmp_path, monkeypatch):
    pipeline = _pipeline(tmp_path, monkeypatch)
    project = ComicProject(id="t", characters=[CharacterProfile(id="b", name="Bruno", backstory="x")])
    with pytest.raises(ValueError):
        pipeline.approve_bible(project, "b")


def test_product_page_renders_with_and_without_photo(tmp_path):
    photo = tmp_path / "p.png"
    Image.new("RGB", (900, 1600), "brown").save(photo)
    assert render_product_page(CharacterProfile(id="b", name="Bruno", backstory="x"), photo).size == PAGE_SIZE
    assert render_product_page(CharacterProfile(id="b", name="Bruno", backstory="x"), None).size == PAGE_SIZE
