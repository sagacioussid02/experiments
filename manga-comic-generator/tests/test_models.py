from app.models import CharacterProfile, ComicPage, ComicProject, DialogueLine, Panel, StoryArc


def test_comic_project_round_trips_through_json():
    character = CharacterProfile(
        id="char_0",
        name="Kaya",
        backstory="A retired storm-chaser.",
        personality="Gruff but protective",
        visual_description="Tall, silver undercut, weathered coat, eye patch",
    )
    panel = Panel(
        panel_number=1,
        characters=["Kaya"],
        scene_description="Kaya stares down an approaching storm from the lighthouse balcony.",
        dialogue=[DialogueLine(character="Kaya", text="One more night.")],
    )
    project = ComicProject(
        id="proj_0",
        characters=[character],
        story=StoryArc(
            title="Last Light",
            genre="drama",
            logline="A lighthouse keeper faces one final storm.",
            synopsis="Kaya must decide whether to abandon her post.",
            chapters=["Setup", "Storm hits", "Resolution"],
        ),
        pages=[ComicPage(page_number=1, panels=[panel])],
    )

    restored = ComicProject.model_validate_json(project.model_dump_json())

    assert restored == project
    assert restored.pages[0].panels[0].dialogue[0].character == "Kaya"
