from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import CharacterProfile
from app.pipeline.story_generator import StoryGenerator


def _fake_tool_response(input_dict: dict) -> SimpleNamespace:
    tool_use_block = SimpleNamespace(type="tool_use", input=input_dict)
    return SimpleNamespace(content=[tool_use_block])


def test_generate_parses_forced_tool_call_into_story_arc():
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_tool_response(
        {
            "title": "Last Light",
            "genre": "drama",
            "logline": "A lighthouse keeper faces one final storm.",
            "synopsis": "Kaya must decide whether to abandon her post before the storm hits.",
            "chapters": ["Setup", "Storm hits", "Resolution"],
        }
    )

    generator = StoryGenerator(client=fake_client, model="claude-sonnet-5")
    characters = [
        CharacterProfile(id="char_0", name="Kaya", backstory="A retired storm-chaser.")
    ]

    story = generator.generate(characters, theme="one last night at the lighthouse")

    assert story.title == "Last Light"
    assert story.chapters == ["Setup", "Storm hits", "Resolution"]

    _, kwargs = fake_client.messages.create.call_args
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_story_arc"}
    assert "Kaya" in kwargs["messages"][0]["content"]
