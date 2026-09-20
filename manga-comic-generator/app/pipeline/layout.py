from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from app.models import CharacterProfile, ComicPage, DialogueLine, Panel

PAGE_SIZE = (1600, 2400)
MARGIN = 60
GUTTER = 28
SLANT = 46  # horizontal offset of a vertical gutter between its top and bottom, in page px
BORDER = 6
SUPERSAMPLE = 2  # everything is drawn at 2x and downscaled so edges are anti-aliased
BASE_FONT_PX = 34
MIN_FONT_PX = 20
PDF_DPI = 188  # 1600px / 8.5in

# Row templates: how many panels go in each row for a run of n consecutive non-splash panels.
_ROW_TEMPLATES = {
    1: [1],
    2: [2],
    3: [1, 2],
    4: [2, 2],
    5: [2, 3],
    6: [2, 2, 2],
}

_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Comic Sans MS Bold.ttf",
    "/System/Library/Fonts/Supplemental/Chalkboard.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


@dataclass
class PanelBox:
    panel: Panel
    polygon: list[tuple[float, float]]  # in page px (1x)

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [x for x, _ in self.polygon]
        ys = [y for _, y in self.polygon]
        return min(xs), min(ys), max(xs), max(ys)


def load_font(size: int, font_path: str | None = None) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in ([font_path] if font_path else []) + _FONT_CANDIDATES:
        if candidate and Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default(size=size)  # scalable, unlike the old bitmap default


# --------------------------------------------------------------------------- page layout


def _rows_for(panels: list[Panel]) -> list[list[Panel]]:
    """Group panels into rows. Splash panels (importance 3) get a row to themselves; the runs
    between them are split by _ROW_TEMPLATES so pages alternate wide and narrow panels."""
    rows: list[list[Panel]] = []
    run: list[Panel] = []

    def flush() -> None:
        while run:
            take = run[:6]
            del run[:6]
            index = 0
            for count in _ROW_TEMPLATES[len(take)]:
                rows.append(take[index : index + count])
                index += count

    for panel in panels:
        if panel.importance >= 3:
            flush()
            rows.append([panel])
        else:
            run.append(panel)
    flush()
    return rows


def _row_weight(row: list[Panel]) -> float:
    if any(p.importance >= 3 for p in row):
        return 2.2
    if any(p.importance == 2 for p in row):
        return 1.25
    return 1.0


def plan_page(page: ComicPage, page_size: tuple[int, int] = PAGE_SIZE) -> list[PanelBox]:
    """Compute each panel's polygon. Horizontal gutters are straight; vertical gutters are
    slanted (alternating direction per row) for the dynamic look of manga pages."""
    width, height = page_size
    rows = _rows_for(page.panels)
    if not rows:
        return []

    weights = [_row_weight(row) for row in rows]
    usable_h = height - 2 * MARGIN - (len(rows) - 1) * GUTTER
    left, right = MARGIN, width - MARGIN
    boxes: list[PanelBox] = []
    y0 = float(MARGIN)

    for row_index, (row, weight) in enumerate(zip(rows, weights)):
        row_h = usable_h * weight / sum(weights)
        y1 = y0 + row_h
        count = len(row)
        usable_w = right - left - (count - 1) * GUTTER
        if count == 2:
            ratios = [0.58, 0.42] if row_index % 2 == 0 else [0.42, 0.58]
        else:
            ratios = [1 / count] * count

        # x of each gutter's centre line at the top of the row, then slant it toward the bottom
        slant = SLANT if row_index % 2 == 0 else -SLANT
        edges_top = [float(left)]
        cursor = float(left)
        for ratio in ratios[:-1]:
            cursor += usable_w * ratio
            edges_top.append(cursor + GUTTER / 2)
            cursor += GUTTER
        edges_top.append(float(right))
        edges_bottom = [edges_top[0]] + [x - slant for x in edges_top[1:-1]] + [edges_top[-1]]
        # Convert gutter centre-lines into per-panel left/right edges.
        for i, panel in enumerate(row):
            lt = edges_top[i] + (GUTTER / 2 if i > 0 else 0)
            lb = edges_bottom[i] + (GUTTER / 2 if i > 0 else 0)
            rt = edges_top[i + 1] - (GUTTER / 2 if i < count - 1 else 0)
            rb = edges_bottom[i + 1] - (GUTTER / 2 if i < count - 1 else 0)
            boxes.append(PanelBox(panel, [(lt, y0), (rt, y0), (rb, y1), (lb, y1)]))
        y0 = y1 + GUTTER
    return boxes


def orientation_for(box: PanelBox) -> str:
    x0, y0, x1, y1 = box.bbox
    ratio = (x1 - x0) / (y1 - y0)
    if ratio > 1.25:
        return "landscape"
    if ratio < 0.8:
        return "portrait"
    return "square"


def assign_orientations(page: ComicPage) -> None:
    """Set panel.orientation from the layout so the image backend can request art in a
    matching aspect ratio (otherwise cover-cropping throws away much of the picture)."""
    for box in plan_page(page):
        box.panel.orientation = orientation_for(box)


# --------------------------------------------------------------------------- text + bubbles


def _wrap(text: str, font, max_width: float) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if current and font.getlength(trial) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    return lines or [""]


def _text_block(text: str, font, max_width: float) -> tuple[str, float, float]:
    lines = _wrap(text, font, max_width)
    width = max(font.getlength(line) for line in lines)
    line_h = font.size * 1.2
    return "\n".join(lines), width, line_h * len(lines)


@dataclass
class _Bubble:
    line: DialogueLine
    text: str
    cx: float
    top: float
    rx: float
    ry: float
    speaker_x: float
    text_w: float
    text_h: float


def _place_bubbles(
    lines: list[DialogueLine], names: list[str], bbox, font_px: int, top: float, font_path
) -> tuple[list[_Bubble], float, ImageFont.FreeTypeFont]:
    x0, _, x1, _ = bbox
    width = x1 - x0
    font = load_font(font_px * SUPERSAMPLE, font_path)
    inset = width * 0.05 + SLANT * SUPERSAMPLE / 2
    bubbles: list[_Bubble] = []
    y = top
    for k, line in enumerate(lines):
        text, tw, th = _text_block(line.text.upper(), font, width * 0.5)
        rx, ry = tw / 2 * 1.42 + font_px, th / 2 * 1.42 + font_px * 0.8
        if line.bubble_type == "shout":
            rx, ry = rx * 1.12, ry * 1.12
        n = max(len(names), 1)
        index = names.index(line.character) if line.character in names else None
        fraction = 0.5 if index is None or n == 1 else (index + 0.5) / n
        speaker_x = x0 + fraction * width
        # Stagger consecutive bubbles from a lone speaker so they don't stack in one column.
        cx = speaker_x + (-1) ** k * width * 0.1 * (n == 1 and len(lines) > 1)
        cx = min(max(cx, x0 + inset + rx), x1 - inset - rx)
        bubbles.append(_Bubble(line, text, cx, y, rx, ry, speaker_x, tw, th))
        y += 2 * ry + font_px * 0.7
    return bubbles, y - top, font


def _draw_burst(draw: ImageDraw.ImageDraw, cx, cy, rx, ry, fill, outline, border) -> None:
    points = []
    spikes = 16
    for i in range(spikes * 2):
        angle = math.pi * i / spikes
        scale = 1.12 if i % 2 == 0 else 0.86
        points.append((cx + math.cos(angle) * rx * scale, cy + math.sin(angle) * ry * scale))
    draw.polygon(points, fill=outline)
    inner = []
    for px, py in points:
        inner.append((cx + (px - cx) * (1 - border / max(rx, 1)), cy + (py - cy) * (1 - border / max(ry, 1))))
    draw.polygon(inner, fill=fill)


def _draw_bubble(draw: ImageDraw.ImageDraw, b: _Bubble, font, panel_bottom: float, border: float) -> None:
    cy = b.top + b.ry
    kind = b.line.bubble_type
    if kind == "shout":
        _draw_burst(draw, b.cx, cy, b.rx, b.ry, "white", "black", border)
    else:
        draw.ellipse([b.cx - b.rx - border, cy - b.ry - border, b.cx + b.rx + border, cy + b.ry + border], fill="black")
        tip_y = min(cy + b.ry + font.size * 1.6, panel_bottom - font.size)
        dx = max(-b.rx * 0.5, min(b.rx * 0.5, b.speaker_x - b.cx))
        base_y = cy + b.ry * math.sqrt(max(0.0, 1 - (dx / b.rx) ** 2))
        if kind == "thought":
            for i, (frac, radius) in enumerate([(0.35, 0.34), (0.68, 0.24), (0.95, 0.15)]):
                tx = b.cx + dx + (b.speaker_x - b.cx - dx) * frac
                ty = base_y + (tip_y - base_y) * frac + font.size * 0.25
                r = font.size * radius * 1.6
                draw.ellipse([tx - r - border, ty - r - border, tx + r + border, ty + r + border], fill="black")
                draw.ellipse([tx - r, ty - r, tx + r, ty + r], fill="white")
            draw.ellipse([b.cx - b.rx, cy - b.ry, b.cx + b.rx, cy + b.ry], fill="white")
        else:
            half = min(b.rx * 0.22, font.size * 0.7)
            tail = [(b.cx + dx - half, base_y - font.size * 0.4), (b.cx + dx + half, base_y - font.size * 0.4), (b.speaker_x, tip_y)]
            draw.polygon(tail, fill="black")
            draw.line(tail + [tail[0]], fill="black", width=int(border * 2), joint="curve")
            draw.ellipse([b.cx - b.rx, cy - b.ry, b.cx + b.rx, cy + b.ry], fill="white")
            draw.polygon(tail, fill="white")
    draw.multiline_text((b.cx, cy), b.text, fill="black", font=font, anchor="mm", align="center", spacing=font.size * 0.2)


def _draw_caption(draw: ImageDraw.ImageDraw, text: str, bbox, font, top: float, border: float) -> float:
    x0, _, x1, _ = bbox
    pad = font.size * 0.5
    inset = SLANT * SUPERSAMPLE / 2 + 12 * SUPERSAMPLE
    block, tw, th = _text_block(text, font, (x1 - x0) * 0.7)
    left = x0 + inset
    draw.rectangle([left, top, left + tw + 2 * pad, top + th + 2 * pad], fill=(255, 247, 214), outline="black", width=int(border * 0.7))
    draw.multiline_text((left + pad, top + pad), block, fill="black", font=font, spacing=font.size * 0.2)
    return top + th + 2 * pad + font.size * 0.5


def _draw_lettering(draw: ImageDraw.ImageDraw, box: PanelBox, font_path: str | None) -> None:
    panel = box.panel
    if not panel.caption and not panel.dialogue:
        return
    s = SUPERSAMPLE
    x0, y0, x1, y1 = (v * s for v in box.bbox)
    bbox = (x0, y0, x1, y1)
    height = y1 - y0
    margin_top = y0 + 18 * s

    top = margin_top
    if panel.caption:
        top = _draw_caption(draw, panel.caption, bbox, load_font(int(BASE_FONT_PX * 0.85) * s, font_path), top, BORDER * s)

    # Shrink the lettering until the bubble stack fits in the top ~55% of the panel.
    budget = (y0 + height * 0.6) - top
    for font_px in range(BASE_FONT_PX, MIN_FONT_PX - 1, -2):
        bubbles, used, font = _place_bubbles(panel.dialogue, panel.characters, bbox, font_px, top, font_path)
        if used <= budget:
            break
    for bubble in bubbles:
        _draw_bubble(draw, bubble, font, y1, BORDER * 0.5 * s)


# --------------------------------------------------------------------------- rendering


def render_page(page: ComicPage, panel_images: dict[int, Path], font_path: str | None = None) -> Image.Image:
    s = SUPERSAMPLE
    canvas = Image.new("RGB", (PAGE_SIZE[0] * s, PAGE_SIZE[1] * s), "white")
    draw = ImageDraw.Draw(canvas)
    boxes = plan_page(page)

    for box in boxes:
        polygon = [(x * s, y * s) for x, y in box.polygon]
        x0, y0, x1, y1 = (int(round(v * s)) for v in box.bbox)
        w, h = x1 - x0, y1 - y0
        image_path = panel_images.get(box.panel.panel_number)

        if image_path and Path(image_path).exists():
            art = ImageOps.fit(Image.open(image_path).convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, 0.4))
        else:
            art = Image.new("RGB", (w, h), (232, 232, 232))
            ImageDraw.Draw(art).text((w // 2, h // 2), f"PANEL {box.panel.panel_number}", fill=(150, 150, 150), font=load_font(40 * s, font_path), anchor="mm")

        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).polygon([(px - x0, py - y0) for px, py in polygon], fill=255)
        canvas.paste(art, (x0, y0), mask)
        draw.line(polygon + [polygon[0]], fill="black", width=BORDER * s, joint="curve")

    # Lettering goes on last so bubbles can overlap a panel's edge region without being clipped.
    for box in boxes:
        _draw_lettering(draw, box, font_path)

    return canvas.resize(PAGE_SIZE, Image.LANCZOS)


def _draw_label(draw: ImageDraw.ImageDraw, text: str, x: int, y: int, font) -> int:
    """Black tab with white text (section heading); returns the y just below it."""
    pad = font.size * 0.35
    w = font.getlength(text)
    draw.rectangle([x, y, x + w + 2 * pad, y + font.size * 1.3 + pad], fill="black")
    draw.text((x + pad, y + pad * 0.7), text, fill="white", font=font)
    return int(y + font.size * 1.3 + pad + 12 * SUPERSAMPLE)


def render_character_page(
    character: CharacterProfile, sheet_path: str | Path | None = None, font_path: str | None = None
) -> Image.Image:
    """A 'character file' page: banner, name, the character sheet, then backstory and personality.
    Text comes straight from the profile the user wrote; body text shrinks to fit."""
    s = SUPERSAMPLE
    W, H = PAGE_SIZE[0] * s, PAGE_SIZE[1] * s
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    m = MARGIN * s
    draw.rectangle([m // 2, m // 2, W - m // 2, H - m // 2], outline="black", width=BORDER * s)

    # banner
    banner = [m, m, W - m, m + 130 * s]
    draw.rectangle(banner, fill="black")
    draw.text((W // 2, m + 65 * s), "CHARACTER FILE", fill="white", font=load_font(64 * s, font_path), anchor="mm")

    # name
    draw.text((m, m + 150 * s), character.name.upper(), fill="black", font=load_font(130 * s, font_path))

    # sheet / portrait
    top = m + 330 * s
    art_w = W - 2 * m
    art_h = 940 * s
    sheet = Image.open(sheet_path).convert("RGB") if sheet_path and Path(sheet_path).exists() else None
    if sheet:
        # Size the box to the sheet's own aspect ratio so none of the design is cropped.
        art_h = min(int(art_w * sheet.height / sheet.width), 1100 * s)
    box = (m, top, W - m, top + art_h)
    if sheet:
        canvas.paste(ImageOps.fit(sheet, (art_w, art_h), Image.LANCZOS), (box[0], box[1]))
    else:
        draw.rectangle(box, fill=(238, 238, 238))
        draw.text(((box[0] + box[2]) // 2, top + art_h // 2), "NO PORTRAIT", fill=(150, 150, 150), font=load_font(48 * s, font_path), anchor="mm")
    draw.rectangle(box, outline="black", width=BORDER * s)

    # text sections, shrunk until both fit the space below the art
    sections = [
        ("BACKSTORY", character.backstory),
        ("PERSONALITY", character.personality),
        ("APPEARANCE", character.visual_description),
    ]
    sections = [(t, body) for t, body in sections if body.strip()]
    y_start = top + art_h + 30 * s
    y_end = H - m - 10 * s
    width = W - 2 * m
    for size in range(52, 23, -2):
        font = load_font(size * s, font_path)
        head = load_font(int(size * 0.9) * s, font_path)
        total = 0.0
        laid = []
        for title, body in sections:
            lines = _wrap(body, font, width)
            laid.append((title, lines))
            total += head.size * 1.3 + head.size * 0.35 + 12 * s + len(lines) * font.size * 1.3 + 24 * s
        if y_start + total <= y_end:
            break
    y = y_start
    for title, lines in laid:
        y = _draw_label(draw, title, m, int(y), head)
        for line in lines:
            draw.text((m, y), line, fill="black", font=font)
            y += font.size * 1.3
        y += 24 * s

    return canvas.resize(PAGE_SIZE, Image.LANCZOS)


def _fit_title(title: str, width: int, max_height: int, font_path: str | None) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont]:
    """Largest title font (<= 3 lines, each within `width`) -- in supersampled px."""
    for size in range(230, 79, -10):
        font = load_font(size * SUPERSAMPLE, font_path)
        lines = _wrap(title.upper(), font, width)
        if len(lines) <= 3 and all(font.getlength(line) <= width for line in lines) and len(lines) * font.size * 1.08 <= max_height:
            return lines, font
    font = load_font(80 * SUPERSAMPLE, font_path)
    return _wrap(title.upper(), font, width)[:3], font


def _fit_tagline(text: str, width: int, font_path: str | None, max_lines: int = 3):
    """Shrink the tagline until it fits in max_lines; only if it never does, cut at a word with '…'."""
    for size in range(44, 29, -2):
        font = load_font(size * SUPERSAMPLE, font_path)
        lines = _wrap(text, font, width)
        if len(lines) <= max_lines:
            return lines, font
    lines = _wrap(text, font, width)[:max_lines]
    lines[-1] = lines[-1].rstrip(" ,.;:") + "…"
    return lines, font


def render_cover_page(
    title: str,
    tagline: str,
    art_path: str | Path | None,
    starring: list[str],
    fallback_sheet: str | Path | None = None,
    font_path: str | None = None,
) -> Image.Image:
    """Cover: full-bleed art in a heavy frame, outlined title lettering across the top, and a
    black band at the bottom with 'starring' and the tagline. The art prompt leaves the top and
    bottom open for these. Without art, the character sheet (if any) is centred instead."""
    s = SUPERSAMPLE
    W, H = PAGE_SIZE[0] * s, PAGE_SIZE[1] * s
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    m = 48 * s
    frame = (m, m, W - m, H - m)
    fw, fh = frame[2] - frame[0], frame[3] - frame[1]

    source = art_path if art_path and Path(art_path).exists() else fallback_sheet
    if source and Path(source).exists():
        img = Image.open(source).convert("RGB")
        if source == art_path:
            canvas.paste(ImageOps.fit(img, (fw, fh), Image.LANCZOS, centering=(0.5, 0.4)), (frame[0], frame[1]))
        else:  # a landscape sheet: keep it whole, centred on a pale ground
            draw.rectangle(frame, fill=(240, 240, 240))
            img.thumbnail((fw - 80 * s, fh // 2))
            canvas.paste(img, (frame[0] + (fw - img.width) // 2, frame[1] + (fh - img.height) // 2))
    else:
        draw.rectangle(frame, fill=(240, 240, 240))
    draw.rectangle(frame, outline="black", width=10 * s)

    # title, centred, white with a heavy black outline so it reads over any art
    lines, tfont = _fit_title(title, fw - 120 * s, int(fh * 0.24), font_path)
    y = frame[1] + 70 * s
    for line in lines:
        draw.text((W // 2, y), line, font=tfont, fill="white", stroke_width=12 * s, stroke_fill="black", anchor="ma")
        y += int(tfont.size * 1.08)

    # bottom band
    small = load_font(38 * s, font_path)
    tag_lines, tag_font = _fit_tagline(tagline, fw - 160 * s, font_path) if tagline else ([], load_font(44 * s, font_path))
    star = "STARRING " + " & ".join(n.upper() for n in starring) if starring else ""
    band_h = int((small.size * 1.5 if star else 0) + len(tag_lines) * tag_font.size * 1.3 + 70 * s)
    top = frame[3] - band_h
    draw.rectangle([frame[0], top, frame[2], frame[3]], fill="black")
    ty = top + 30 * s
    if star:
        draw.text((W // 2, ty), star, font=small, fill=(255, 226, 120), anchor="ma")
        ty += int(small.size * 1.5)
    for line in tag_lines:
        draw.text((W // 2, ty), line, font=tag_font, fill="white", anchor="ma")
        ty += int(tag_font.size * 1.3)
    draw.rectangle(frame, outline="black", width=10 * s)

    return canvas.resize(PAGE_SIZE, Image.LANCZOS)


def render_product_page(character: CharacterProfile, photo_path: str | Path | None, font_path: str | None = None) -> Image.Image:
    """Back page: the real handmade product photo with its name and a short line, so the comic ends
    on the thing the buyer actually owns."""
    s = SUPERSAMPLE
    W, H = PAGE_SIZE[0] * s, PAGE_SIZE[1] * s
    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)
    m = MARGIN * s
    draw.rectangle([m // 2, m // 2, W - m // 2, H - m // 2], outline="black", width=BORDER * s)
    draw.rectangle([m, m, W - m, m + 130 * s], fill="black")
    draw.text((W // 2, m + 65 * s), "THE REAL " + character.name.upper(), fill="white", font=load_font(64 * s, font_path), anchor="mm")
    top, bottom = m + 170 * s, H - m - 330 * s
    box = (m, top, W - m, bottom)
    if photo_path and Path(photo_path).exists():
        photo = ImageOps.exif_transpose(Image.open(photo_path)).convert("RGB")
        canvas.paste(ImageOps.fit(photo, (box[2] - box[0], box[3] - box[1]), Image.LANCZOS, centering=(0.5, 0.45)), (box[0], box[1]))
    else:
        draw.rectangle(box, fill=(238, 238, 238))
    draw.rectangle(box, outline="black", width=BORDER * s)
    line = f"{character.name} is handmade. This is the one you can hold."
    font = load_font(46 * s, font_path)
    y = bottom + 50 * s
    for text in _wrap(line, font, W - 2 * m):
        draw.text((W // 2, y), text, fill="black", font=font, anchor="ma")
        y += int(font.size * 1.35)
    return canvas.resize(PAGE_SIZE, Image.LANCZOS)


def render_pages_to_pdf(pages: list[Image.Image], output_path: Path) -> Path:
    if not pages:
        raise ValueError("No pages to export")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(output_path, "PDF", save_all=True, append_images=pages[1:], resolution=PDF_DPI)
    return output_path
