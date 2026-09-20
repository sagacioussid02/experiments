import pytest

from app.pipeline.image_generator import OpenAIImageGenerator, get_image_generator


def test_openai_backend_requires_api_key():
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        OpenAIImageGenerator(api_key="")


def test_openai_backend_can_be_selected():
    generator = OpenAIImageGenerator(api_key="test-key")

    assert isinstance(generator, OpenAIImageGenerator)

def test_openai_uses_edits_with_reference_photo_and_orientation_size(tmp_path, monkeypatch):
    import base64
    from unittest.mock import MagicMock

    from app.models import CharacterProfile, Panel
    from app.pipeline import image_generator as module

    photo = tmp_path / "kaya.png"
    from PIL import Image

    Image.new("RGB", (2000, 3000), "brown").save(photo)
    response = MagicMock()
    response.json.return_value = {"data": [{"b64_json": base64.b64encode(b"img").decode()}]}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(module.requests, "post", post)

    panel = Panel(panel_number=1, characters=["Kaya"], scene_description="x", orientation="landscape")
    kaya = CharacterProfile(id="c", name="Kaya", backstory="b", visual_description="silver hair", reference_image_path=str(photo))
    out = OpenAIImageGenerator(api_key="k", input_fidelity="high", reference_max_side=1024).generate_panel(panel, [kaya], tmp_path / "p.png")

    assert out.read_bytes() == b"img"
    assert post.call_args.args[0].endswith("/images/edits")
    assert post.call_args.kwargs["data"]["size"] == "1536x1024"
    assert post.call_args.kwargs["data"]["input_fidelity"] == "high"
    assert post.call_args.kwargs["files"][0][0] == "image[]"
    sent = Image.open(__import__("io").BytesIO(post.call_args.kwargs["files"][0][1][1]))
    assert max(sent.size) == 1024


def test_openai_uses_generations_without_reference(tmp_path, monkeypatch):
    import base64
    from unittest.mock import MagicMock

    from app.models import Panel
    from app.pipeline import image_generator as module

    response = MagicMock()
    response.json.return_value = {"data": [{"b64_json": base64.b64encode(b"img").decode()}]}
    post = MagicMock(return_value=response)
    monkeypatch.setattr(module.requests, "post", post)

    panel = Panel(panel_number=1, characters=[], scene_description="x", orientation="portrait")
    OpenAIImageGenerator(api_key="k").generate_panel(panel, [], tmp_path / "p.png")

    assert post.call_args.args[0].endswith("/images/generations")
    assert post.call_args.kwargs["json"]["size"] == "1024x1536"


def _kaya_panel(**kw):
    from app.models import CharacterProfile, Panel

    kaya = CharacterProfile(id="c", name="Kaya", backstory="b", visual_description="silver hair")
    return Panel(panel_number=1, characters=kw.pop("characters", ["Kaya"]), scene_description="A storm.", **kw), [kaya]


def test_panel_prompt_restricts_characters_and_bans_all_text():
    from app.pipeline.image_generator import build_panel_prompt

    panel, chars = _kaya_panel()
    prompt = build_panel_prompt(panel, chars)
    assert "Draw ONLY these characters: Kaya" in prompt
    assert "onomatopoeia" in prompt and "sound effects" in prompt
    assert "claws, fangs" in prompt


def test_panel_prompt_with_no_characters_draws_setting_only():
    from app.pipeline.image_generator import build_panel_prompt

    panel, chars = _kaya_panel(characters=[])
    assert "Draw no characters" in build_panel_prompt(panel, chars)


def test_sheet_prompt_demands_pure_white_background():
    from app.models import CharacterProfile
    from app.pipeline.image_generator import build_sheet_prompt

    assert "Pure white background" in build_sheet_prompt(CharacterProfile(id="c", name="K", backstory="b"))


def test_script_prompt_forbids_sound_effects_in_scene_descriptions():
    from app.pipeline.script_generator import SYSTEM_PROMPT

    assert "never mention sound effects" in SYSTEM_PROMPT
