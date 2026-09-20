from PIL import Image

from app.models import ComicPage, DialogueLine, Panel
from app.pipeline.layout import (
    PAGE_SIZE,
    _rows_for,
    assign_orientations,
    plan_page,
    render_page,
    render_pages_to_pdf,
)


def _panel(n, importance=1, **kwargs):
    return Panel(panel_number=n, characters=["Kaya", "Ren"], scene_description="x", importance=importance, **kwargs)


def test_rows_vary_with_panel_count_and_splash_gets_own_row():
    assert [len(r) for r in _rows_for([_panel(i) for i in range(1, 5)])] == [2, 2]
    assert [len(r) for r in _rows_for([_panel(i) for i in range(1, 4)])] == [1, 2]
    rows = _rows_for([_panel(1), _panel(2), _panel(3, importance=3), _panel(4)])
    assert [[p.panel_number for p in r] for r in rows] == [[1, 2], [3], [4]]


def test_panels_stay_on_the_page_and_do_not_overlap_rows():
    page = ComicPage(page_number=1, panels=[_panel(i) for i in range(1, 7)])
    boxes = plan_page(page)
    assert len(boxes) == 6
    for box in boxes:
        x0, y0, x1, y1 = box.bbox
        assert 0 <= x0 < x1 <= PAGE_SIZE[0] and 0 <= y0 < y1 <= PAGE_SIZE[1]


def test_splash_panel_is_taller_and_orientations_are_assigned():
    page = ComicPage(page_number=1, panels=[_panel(1), _panel(2, importance=3)])
    boxes = plan_page(page)
    heights = [b.bbox[3] - b.bbox[1] for b in boxes]
    assert heights[1] > heights[0]
    solo = ComicPage(page_number=2, panels=[_panel(1)])
    assign_orientations(solo)
    assert solo.panels[0].orientation == "portrait"


def test_render_page_with_art_and_all_bubble_types(tmp_path):
    art = tmp_path / "art.png"
    Image.new("RGB", (1024, 1024), (120, 140, 160)).save(art)
    page = ComicPage(
        page_number=1,
        panels=[
            _panel(
                1,
                caption="That night.",
                dialogue=[
                    DialogueLine(character="Kaya", text="Hold the light, Ren. It is going to be a long night."),
                    DialogueLine(character="Ren", text="I can't hold it.", bubble_type="thought"),
                    DialogueLine(character="Kaya", text="NOW!", bubble_type="shout"),
                ],
            ),
            _panel(2),
        ],
    )

    image = render_page(page, panel_images={1: art, 2: art})

    assert image.size == PAGE_SIZE
    pdf = render_pages_to_pdf([image, image], tmp_path / "out.pdf")
    assert pdf.read_bytes().startswith(b"%PDF")


def test_render_page_without_art_still_works():
    page = ComicPage(page_number=1, panels=[_panel(1, dialogue=[DialogueLine(character="Kaya", text="Hi.")])])
    assert render_page(page, panel_images={}).size == PAGE_SIZE
