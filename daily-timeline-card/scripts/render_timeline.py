#!/usr/bin/env python3
"""Validate and render a proportional daily timeline card.

The script intentionally accepts normalized JSON rather than guessing natural
language. The skill performs the conversational parsing and confirmation; this
renderer owns chronology, totals, geometry, and output consistency.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import re
import sys
import tempfile
import zlib
from collections import defaultdict
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:  # pragma: no cover - handled at runtime
    Image = ImageDraw = ImageFont = None


WIDTH = 1080
RATIO_DIMENSIONS = {
    "1:1": (1080, 1080),
    "3:4": (1080, 1440),
}
CHART_TOP = 112
AXIS_X, BLOCK_X, BLOCK_RIGHT = 166, 192, 1032
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")
CJK_RE = re.compile(r"[\u2e80-\u9fff\uf900-\ufaff]")
FIXED_CATEGORIES = [
    ("sleep-life", "睡眠/生活"),
    ("study-job", "学习/找工作"),
    ("meals", "饮食"),
    ("leisure-social", "娱乐社交"),
]
WATERMARK_TEXT = "daily-timeline-card · zoey"
WATERMARK_FONT_SIZE = 15
WATERMARK_OPACITY = 0.28


class ConfigError(ValueError):
    pass


def canvas_dimensions(config: dict[str, Any]) -> tuple[int, int]:
    ratio = str(config.get("ratio", "3:4")).strip()
    if ratio not in RATIO_DIMENSIONS:
        supported = ", ".join(RATIO_DIMENSIONS)
        raise ConfigError(f"Unsupported ratio {ratio!r}; choose {supported}.")
    return RATIO_DIMENSIONS[ratio]


def vertical_layout(height: int) -> tuple[int, int, int]:
    """Return chart bottom, divider y, and legend top for the canvas height."""
    return height - 192, height - 157, height - 129


def separated_label_positions(
    anchors: list[float], top: float, bottom: float, minimum_gap: float = 28
) -> list[float]:
    """Keep time labels readable without moving their exact tick marks."""
    positions = list(anchors)
    for index in range(1, len(positions)):
        positions[index] = max(positions[index], positions[index - 1] + minimum_gap)
    if positions and positions[-1] > bottom:
        positions[-1] = bottom
        for index in range(len(positions) - 2, -1, -1):
            positions[index] = min(positions[index], positions[index + 1] - minimum_gap)
    if positions and positions[0] < top:
        positions[0] = top
        for index in range(1, len(positions)):
            positions[index] = max(positions[index], positions[index - 1] + minimum_gap)
    return positions


@dataclass
class Event:
    start_text: str
    end_text: str
    label: str
    category: str
    start: int
    end: int

    @property
    def minutes(self) -> int:
        return self.end - self.start


def parse_time(value: str) -> int:
    match = TIME_RE.fullmatch(str(value).strip())
    if not match:
        raise ConfigError(f"Invalid time {value!r}; use HH:MM.")
    hour, minute = map(int, match.groups())
    if hour == 24 and minute == 0:
        return 1440
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"Invalid time {value!r}.")
    return hour * 60 + minute


def compact_duration(minutes: int) -> str:
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours}h {mins}min"
    if hours:
        return f"{hours}h"
    return f"{mins}min"


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def rgb_to_hex(value: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02X}" for channel in value)


def watermark_color(background: str, categories: list[dict[str, str]]) -> str:
    """Blend the category palette into the background for a quiet adaptive credit."""
    palette = [hex_to_rgb(category["color"]) for category in categories]
    average = tuple(round(sum(color[channel] for color in palette) / len(palette)) for channel in range(3))
    backdrop = hex_to_rgb(background)
    blended = tuple(
        round(backdrop[channel] * (1 - WATERMARK_OPACITY) + average[channel] * WATERMARK_OPACITY)
        for channel in range(3)
    )
    return rgb_to_hex(blended)


def clock_text(minutes: int) -> str:
    value = minutes % 1440
    return f"{value // 60:02d}:{value % 60:02d}"


def normalize_events(raw_events: list[dict[str, Any]]) -> list[Event]:
    if not raw_events:
        raise ConfigError("At least one event is required.")

    events: list[Event] = []
    previous_end: int | None = None
    for index, raw in enumerate(raw_events, 1):
        for key in ("start", "end", "label", "category"):
            if key not in raw or str(raw[key]).strip() == "":
                raise ConfigError(f"Event {index} is missing {key!r}.")

        start_raw = parse_time(raw["start"])
        end_raw = parse_time(raw["end"])
        start = start_raw
        if previous_end is not None:
            while start < previous_end:
                start += 1440
        end = end_raw
        while end <= start:
            end += 1440

        if previous_end is not None and start != previous_end:
            relation = "gap" if start > previous_end else "overlap"
            raise ConfigError(
                f"Schedule {relation}: event {index} starts at {raw['start']} "
                f"but the previous event ends at {clock_text(previous_end)}."
            )

        event = Event(
            start_text=str(raw["start"]),
            end_text=str(raw["end"]),
            label=str(raw["label"]).strip(),
            category=str(raw["category"]).strip(),
            start=start,
            end=end,
        )
        events.append(event)
        previous_end = end

    return events


def relative_path(config_path: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def validate_config(
    config: dict[str, Any], config_path: Path, require_confirmed: bool = True
) -> tuple[list[Event], list[dict[str, str]], dict[str, int]]:
    if require_confirmed:
        if config.get("classification_confirmed") is not True:
            raise ConfigError("Classification is not confirmed; rendering is blocked.")
        if config.get("palette_confirmed") is not True:
            raise ConfigError("Palette is not confirmed; rendering is blocked.")

    canvas_dimensions(config)

    if "day_label" in config:
        raise ConfigError("The public version does not support DAY-series labels.")
    date = str(config.get("date", "")).strip()
    if not date:
        raise ConfigError("date is required.")
    if re.search(r"\bDAY\s*\d+\b", date, re.IGNORECASE):
        raise ConfigError("Remove the DAY-series label from date.")
    for key in ("font_regular", "font_bold"):
        if key in config:
            raise ConfigError("The public version uses its bundled font scheme.")

    categories = config.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ConfigError("categories must be a non-empty list.")

    clean_categories: list[dict[str, str]] = []
    category_ids: set[str] = set()
    for index, item in enumerate(categories, 1):
        if not isinstance(item, dict):
            raise ConfigError(f"Category {index} must be an object.")
        category_id = str(item.get("id", "")).strip()
        label = str(item.get("label", "")).strip()
        color = str(item.get("color", "")).strip().upper()
        if not category_id or not label or not HEX_RE.fullmatch(color):
            raise ConfigError(f"Category {index} needs id, label, and #RRGGBB color.")
        if category_id in category_ids:
            raise ConfigError(f"Duplicate category id: {category_id}")
        category_ids.add(category_id)
        clean_categories.append({"id": category_id, "label": label, "color": color})

    actual_categories = [(item["id"], item["label"]) for item in clean_categories]
    if actual_categories != FIXED_CATEGORIES:
        expected = ", ".join(f"{category_id}:{label}" for category_id, label in FIXED_CATEGORIES)
        raise ConfigError(f"Use the four fixed public categories in order: {expected}.")

    events = normalize_events(config.get("events") or [])
    unknown = sorted({event.category for event in events} - category_ids)
    if unknown:
        raise ConfigError("Unknown event categories: " + ", ".join(unknown))

    span = events[-1].end - events[0].start
    if config.get("require_24_hours", True) and span != 1440:
        raise ConfigError(
            f"The schedule spans {compact_duration(span)}, not 24h. "
            "Fix the ranges or set require_24_hours to false."
        )

    for key in ("background", "ink"):
        value = str(config.get(key, "")).strip()
        if value and not HEX_RE.fullmatch(value):
            raise ConfigError(f"{key} must be a #RRGGBB color.")

    totals: dict[str, int] = defaultdict(int)
    for event in events:
        totals[event.category] += event.minutes

    return events, clean_categories, dict(totals)


def audit_payload(events: list[Event], categories: list[dict[str, str]], totals: dict[str, int]) -> dict[str, Any]:
    return {
        "start": clock_text(events[0].start),
        "end": clock_text(events[-1].end),
        "span_minutes": events[-1].end - events[0].start,
        "events": [
            {
                "start": event.start_text,
                "end": event.end_text,
                "label": event.label,
                "category": event.category,
                "minutes": event.minutes,
                "duration": compact_duration(event.minutes),
            }
            for event in events
        ],
        "totals": [
            {
                "category": category["id"],
                "label": category["label"],
                "minutes": totals.get(category["id"], 0),
                "duration": compact_duration(totals.get(category["id"], 0)),
            }
            for category in categories
        ],
    }


def event_geometry(events: list[Event], chart_bottom: int) -> list[tuple[Event, float, float]]:
    start, span = events[0].start, events[-1].end - events[0].start
    chart_height = chart_bottom - CHART_TOP
    result = []
    for event in events:
        y1 = CHART_TOP + (event.start - start) / span * chart_height
        y2 = CHART_TOP + (event.end - start) / span * chart_height
        result.append((event, y1, y2))
    return result


def svg_text(x: float, y: float, value: str, size: int, weight: int = 400, anchor: str = "start") -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}" fill="currentColor">{escape(value)}</text>'
    )


def render_svg(
    config: dict[str, Any], events: list[Event], categories: list[dict[str, str]], totals: dict[str, int]
) -> str:
    width, height = canvas_dimensions(config)
    canvas_height = height
    chart_bottom, divider_y, legend_top = vertical_layout(height)
    background = config.get("background", "#F7F4EE")
    ink = config.get("ink", "#25364A")
    credit_color = watermark_color(background, categories)
    category_map = {category["id"]: category for category in categories}
    header = str(config.get("date", "")).strip()
    next_prefix = str(config.get("next_day_prefix", "次日"))
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        f'<rect width="{width}" height="{height}" fill="{background}"/>',
        f'<g color="{ink}" font-family="Noto Sans CJK SC, PingFang SC, Microsoft YaHei, sans-serif">',
        svg_text(50, 52, header, 23, 500),
        f'<path d="M50 73H1032" stroke="{ink}" stroke-width="1.4"/>',
        f'<path d="M{AXIS_X} {CHART_TOP-8}V{chart_bottom+8}" stroke="{ink}" stroke-width="1.6"/>',
    ]

    geometry = event_geometry(events, chart_bottom)
    for event, y1, y2 in geometry:
        fill = category_map[event.category]["color"]
        height = y2 - y1
        parts.append(
            f'<rect x="{BLOCK_X}" y="{y1:.2f}" width="{BLOCK_RIGHT-BLOCK_X}" height="{height:.2f}" '
            f'fill="{fill}" stroke="{background}" stroke-width="2"/>'
        )
        center = (y1 + y2) / 2
        if height >= 68:
            parts.append(svg_text(232, center - 5, event.label, 28, 650))
            parts.append(svg_text(232, center + 28, compact_duration(event.minutes), 23, 400))
        else:
            size = 21 if height >= 30 else max(11, min(16, int(height - 3)))
            value = f"{event.label}   {compact_duration(event.minutes)}"
            parts.append(svg_text(232, center + size * 0.36, value, size, 550))

    boundaries = [(events[0].start, events[0].start_text)] + [
        (event.end, event.end_text) for event in events
    ]
    span = events[-1].end - events[0].start
    boundary_ys = [
        CHART_TOP + (minute - events[0].start) / span * (chart_bottom - CHART_TOP)
        for minute, _ in boundaries
    ]
    label_ys = separated_label_positions(boundary_ys, CHART_TOP, chart_bottom)
    for index, ((minute, label), y, label_y) in enumerate(zip(boundaries, boundary_ys, label_ys)):
        display = label
        if index == len(boundaries) - 1 and minute >= 1440:
            display = f"{next_prefix}{clock_text(minute)}"
        label_x = 145 if index == len(boundaries) - 1 else 116
        parts.append(f'<path d="M143 {y:.2f}H{AXIS_X}" stroke="{ink}" stroke-width="1.3"/>')
        parts.append(f'<circle cx="{AXIS_X}" cy="{y:.2f}" r="5.2" fill="{ink}"/>')
        parts.append(svg_text(label_x, label_y + 8, display, 25, 450, "end"))

    parts.append(f'<path d="M50 {divider_y}H1032" stroke="{ink}" stroke-width="1.3"/>')
    rows = math.ceil(len(categories) / 2)
    row_height = min(42, max(30, (height - legend_top - 10) // max(rows, 1)))
    columns = [(62, 116, 510), (570, 624, 1008)]
    for index, category in enumerate(categories):
        column = index % 2
        row = index // 2
        swatch_x, label_x, total_x = columns[column]
        y = legend_top + row * row_height
        parts.append(f'<rect x="{swatch_x}" y="{y}" width="30" height="30" fill="{category["color"]}"/>')
        parts.append(svg_text(label_x, y + 24, category["label"], 21, 500))
        parts.append(svg_text(total_x, y + 24, compact_duration(totals.get(category["id"], 0)), 21, 450, "end"))

    parts.append(
        f'<text x="1032" y="{canvas_height-14}" font-size="{WATERMARK_FONT_SIZE}" font-weight="400" '
        f'text-anchor="end" fill="{credit_color}">{escape(WATERMARK_TEXT)}</text>'
    )

    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts)


def find_font(bold: bool) -> Path | None:
    """Materialize the platform-safe embedded CJK font data on demand."""
    try:
        if bold:
            from font_bold_data import FONT_B85, FONT_SHA256, FONT_SIZE
            filename = "NotoSansCJKsc-Bold-lite.otf"
        else:
            from font_regular_data import FONT_B85, FONT_SHA256, FONT_SIZE
            filename = "NotoSansCJKsc-Regular-lite.otf"
    except ImportError:
        return None

    cache_dir = Path(tempfile.gettempdir()) / "daily-timeline-card-fonts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / filename
    if path.exists() and path.stat().st_size == FONT_SIZE:
        if hashlib.sha256(path.read_bytes()).hexdigest() == FONT_SHA256:
            return path

    payload = zlib.decompress(base64.b85decode(FONT_B85.encode("ascii")))
    if len(payload) != FONT_SIZE or hashlib.sha256(payload).hexdigest() != FONT_SHA256:
        raise ConfigError("Embedded CJK font data is incomplete or corrupted.")
    path.write_bytes(payload)
    return path


def render_png(
    output: Path,
    config: dict[str, Any],
    config_path: Path,
    events: list[Event],
    categories: list[dict[str, str]],
    totals: dict[str, int],
) -> None:
    if Image is None:
        raise ConfigError("Pillow is required for PNG output (`pip install Pillow`).")

    regular_path = find_font(False)
    bold_path = find_font(True)
    if not regular_path:
        raise ConfigError(
            "The embedded CJK font data is unavailable. Re-upload all Python files "
            "from the lightweight package, or generate SVG output instead."
        )
    bold_path = bold_path or regular_path

    font_cache: dict[tuple[bool, int], Any] = {}
    def font(size: int, bold: bool = False):
        key = (bold, size)
        if key not in font_cache:
            font_cache[key] = ImageFont.truetype(str(bold_path if bold else regular_path), size)
        return font_cache[key]

    width, height = canvas_dimensions(config)
    canvas_height = height
    chart_bottom, divider_y, legend_top = vertical_layout(height)
    background = config.get("background", "#F7F4EE")
    ink = config.get("ink", "#25364A")
    credit_color = watermark_color(background, categories)
    image = Image.new("RGB", (width, height), background)
    draw = ImageDraw.Draw(image)
    category_map = {category["id"]: category for category in categories}
    header = str(config.get("date", "")).strip()
    draw.text((50, 31), header, fill=ink, font=font(23))
    draw.line((50, 73, 1032, 73), fill=ink, width=2)
    draw.line((AXIS_X, CHART_TOP - 8, AXIS_X, chart_bottom + 8), fill=ink, width=2)

    def fitted_text(value: str, max_width: int, size: int, bold: bool) -> tuple[str, Any]:
        current = value
        fnt = font(size, bold)
        if draw.textbbox((0, 0), current, font=fnt)[2] <= max_width:
            return current, fnt
        while current and draw.textbbox((0, 0), current + "…", font=fnt)[2] > max_width:
            current = current[:-1]
        return current + "…", fnt

    for event, y1f, y2f in event_geometry(events, chart_bottom):
        y1, y2 = round(y1f), round(y2f)
        fill = category_map[event.category]["color"]
        draw.rectangle((BLOCK_X, y1, BLOCK_RIGHT, y2), fill=fill, outline=background, width=2)
        height, center = y2f - y1f, (y1f + y2f) / 2
        if height >= 68:
            label, label_font = fitted_text(event.label, 760, 28, True)
            draw.text((232, center - 24), label, fill=ink, font=label_font)
            draw.text((232, center + 7), compact_duration(event.minutes), fill=ink, font=font(23))
        else:
            size = 21 if height >= 30 else max(11, min(16, int(height - 3)))
            value, value_font = fitted_text(
                f"{event.label}   {compact_duration(event.minutes)}", 760, size, True
            )
            box = draw.textbbox((0, 0), value, font=value_font)
            draw.text((232, center - (box[3] - box[1]) / 2 - box[1]), value, fill=ink, font=value_font)

    boundaries = [(events[0].start, events[0].start_text)] + [(event.end, event.end_text) for event in events]
    span = events[-1].end - events[0].start
    next_prefix = str(config.get("next_day_prefix", "次日"))
    boundary_ys = [
        CHART_TOP + (minute - events[0].start) / span * (chart_bottom - CHART_TOP)
        for minute, _ in boundaries
    ]
    label_ys = separated_label_positions(boundary_ys, CHART_TOP, chart_bottom)
    for index, ((minute, label), y, label_y) in enumerate(zip(boundaries, boundary_ys, label_ys)):
        display = f"{next_prefix}{clock_text(minute)}" if index == len(boundaries) - 1 and minute >= 1440 else label
        label_x = 145 if index == len(boundaries) - 1 else 116
        draw.line((143, round(y), AXIS_X, round(y)), fill=ink, width=2)
        draw.ellipse((AXIS_X - 5, round(y) - 5, AXIS_X + 5, round(y) + 5), fill=ink)
        time_font = font(25)
        box = draw.textbbox((0, 0), display, font=time_font)
        draw.text((label_x - (box[2] - box[0]), label_y - (box[3] - box[1]) / 2 - box[1]), display, fill=ink, font=time_font)

    draw.line((50, divider_y, 1032, divider_y), fill=ink, width=2)
    rows = math.ceil(len(categories) / 2)
    row_height = min(42, max(30, (height - legend_top - 10) // max(rows, 1)))
    columns = [(62, 116, 510), (570, 624, 1008)]
    for index, category in enumerate(categories):
        column, row = index % 2, index // 2
        swatch_x, label_x, total_x = columns[column]
        y = legend_top + row * row_height
        draw.rectangle((swatch_x, y, swatch_x + 30, y + 30), fill=category["color"])
        draw.text((label_x, y + 2), category["label"], fill=ink, font=font(21, True))
        total_text = compact_duration(totals.get(category["id"], 0))
        total_font = font(21)
        total_box = draw.textbbox((0, 0), total_text, font=total_font)
        draw.text((total_x - (total_box[2] - total_box[0]), y + 2), total_text, fill=ink, font=total_font)

    credit_font = font(WATERMARK_FONT_SIZE)
    credit_box = draw.textbbox((0, 0), WATERMARK_TEXT, font=credit_font)
    credit_width = credit_box[2] - credit_box[0]
    credit_x = 1032 - credit_width - credit_box[0]
    credit_y = canvas_height - 14 - credit_box[3]
    draw.text((credit_x, credit_y), WATERMARK_TEXT, fill=credit_color, font=credit_font)

    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="UTF-8 JSON schedule config")
    parser.add_argument("--png", type=Path, help="PNG output path")
    parser.add_argument("--svg", type=Path, help="SVG output path")
    parser.add_argument("--check", action="store_true", help="validate and print an audit without rendering")
    args = parser.parse_args()

    try:
        config_path = args.config.resolve()
        with config_path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
        events, categories, totals = validate_config(config, config_path, require_confirmed=not args.check)

        if args.check:
            print(json.dumps(audit_payload(events, categories, totals), ensure_ascii=False, indent=2))
            return 0
        if not args.png and not args.svg:
            raise ConfigError("Choose at least one output: --png or --svg.")

        if args.svg:
            args.svg.parent.mkdir(parents=True, exist_ok=True)
            args.svg.write_text(render_svg(config, events, categories, totals), encoding="utf-8")
        if args.png:
            render_png(args.png, config, config_path, events, categories, totals)
        return 0
    except (ConfigError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
