from app.models import ComicPage, DialogueLine, Panel
from app.pipeline.layout import PAGE_SIZE, _grid_for, render_page


def test_grid_for_scales_with_panel_count():
    assert _grid_for(1) == (1, 1)
    assert _grid_for(2) == (2, 1)
    assert _grid_for(4) == (2, 2)
    assert _grid_for(9) == (3, 3)


def test_render_page_produces_correctly_sized_image_without_art():
    page = ComicPage(
        page_number=1,
        panels=[
            Panel(
                panel_number=1,
                characters=["Kaya"],
                scene_description="Kaya braces against the wind.",
                caption="That night.",
                dialogue=[DialogueLine(character="Kaya", text="Hold the light.")],
            ),
            Panel(
                panel_number=2,
                characters=["Kaya"],
                scene_description="The storm breaks over the lighthouse.",
            ),
        ],
    )

    image = render_page(page, panel_images={})

    assert image.size == PAGE_SIZE
