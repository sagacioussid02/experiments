from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.models import ComicPage, Panel

PAGE_SIZE = (1600, 2400)
MARGIN = 24
GUTTER = 16


def _grid_for(n_panels: int) -> tuple[int, int]:
    """Rows/cols for a page given its panel count.

    A simple heuristic, not a real manga layout engine -- real layouts vary panel *size* for
    pacing/emphasis (a climax gets a splash panel), which this doesn't do yet. Good enough for
    a readable page; see DESIGN.md's "next steps" for the real fix.
    """
    if n_panels <= 1:
        return (1, 1)
    if n_panels <= 2:
        return (2, 1)
    if n_panels <= 4:
        return (2, 2)
    if n_panels <= 6:
        return (3, 2)
    return (3, 3)


def render_page(page: ComicPage, panel_images: dict[int, Path], font_path: str | None = None) -> Image.Image:
    rows, cols = _grid_for(len(page.panels))
    canvas = Image.new("RGB", PAGE_SIZE, color="white")
    draw = ImageDraw.Draw(canvas)
    font = ImageFont.truetype(font_path, 28) if font_path else ImageFont.load_default()

    cell_w = (PAGE_SIZE[0] - 2 * MARGIN - (cols - 1) * GUTTER) // cols
    cell_h = (PAGE_SIZE[1] - 2 * MARGIN - (rows - 1) * GUTTER) // rows

    for index, panel in enumerate(page.panels):
        row, col = divmod(index, cols)
        x0 = MARGIN + col * (cell_w + GUTTER)
        y0 = MARGIN + row * (cell_h + GUTTER)
        _draw_panel(canvas, draw, panel, panel_images.get(panel.panel_number), (x0, y0, cell_w, cell_h), font)

    return canvas


def _draw_panel(canvas: Image.Image, draw: ImageDraw.ImageDraw, panel: Panel, image_path, box, font) -> None:
    x0, y0, w, h = box
    draw.rectangle([x0, y0, x0 + w, y0 + h], outline="black", width=3)

    if image_path and Path(image_path).exists():
        art = Image.open(image_path).convert("RGB").resize((w, h))
        canvas.paste(art, (x0, y0))
        draw.rectangle([x0, y0, x0 + w, y0 + h], outline="black", width=3)

    if panel.caption:
        draw.rectangle([x0 + 8, y0 + 8, x0 + w - 8, y0 + 48], fill=(255, 255, 200), outline="black")
        draw.text((x0 + 14, y0 + 14), panel.caption, fill="black", font=font)

    bubble_y = y0 + h - 16
    for line in reversed(panel.dialogue):
        wrapped = textwrap.fill(f"{line.character}: {line.text}", width=32)
        line_height = 22 * (wrapped.count("\n") + 1) + 16
        bubble_y -= line_height
        draw.rounded_rectangle(
            [x0 + 8, bubble_y, x0 + w - 8, bubble_y + line_height],
            radius=12,
            fill="white",
            outline="black",
            width=2,
        )
        draw.text((x0 + 16, bubble_y + 8), wrapped, fill="black", font=font)


def render_pages_to_pdf(pages: list[Image.Image], output_path: Path) -> Path:
    if not pages:
        raise ValueError("No pages to export")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(output_path, save_all=True, append_images=pages[1:])
    return output_path
