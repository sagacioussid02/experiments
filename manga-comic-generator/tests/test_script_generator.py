from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import CharacterProfile, StoryArc
from app.pipeline.script_generator import ScriptGenerator


def _fake_tool_response(input_dict: dict) -> SimpleNamespace:
    tool_use_block = SimpleNamespace(type="tool_use", input=input_dict)
    return SimpleNamespace(content=[tool_use_block])


def test_generate_parses_forced_tool_call_into_pages():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_tool_response(
        {
            "pages": [
                {
                    "page_number": 1,
                    "panels": [
                        {
                            "panel_number": 1,
                            "characters": ["Kaya"],
                            "scene_description": "Kaya braces against the wind on the balcony.",
                            "camera_angle": "wide shot",
                            "caption": "That night.",
                            "dialogue": [
                                {"character": "Kaya", "text": "Hold the light.", "bubble_type": "speech"}
                            ],
                        }
                    ],
                }
            ]
        }
    )

    generator = ScriptGenerator(client=fake_client, model="claude-sonnet-5")
    story = StoryArc(
        title="Last Light",
        genre="drama",
        logline="A lighthouse keeper faces one final storm.",
        synopsis="Kaya must decide whether to abandon her post.",
        chapters=["Setup", "Storm hits", "Resolution"],
    )
    characters = [
        CharacterProfile(
            id="char_0", name="Kaya", backstory="A retired storm-chaser.",
            visual_description="Tall, silver undercut, weathered coat, eye patch",
        )
    ]

    pages = generator.generate(story, characters)

    assert len(pages) == 1
    assert pages[0].panels[0].scene_description.startswith("Kaya braces")
    assert pages[0].panels[0].dialogue[0].text == "Hold the light."

    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_script"}
    assert "eye patch" in kwargs["messages"][0]["content"]
