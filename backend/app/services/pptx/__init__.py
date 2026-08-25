"""PowerPoint テンプレートへの差し込み（⑤）。

テンプレート側の約束ごとは 2 つだけ。

  1. テキストは {{property_name}} のように二重波かっこで書く。
     キーは config/property_fields.json の pptx の値。
  2. 画像を入れたい場所には図形を置き、その図形の**名前**を
     IMG:exterior / IMG:floor_plan / IMG:map / IMG:interior にする。
     図形の位置とサイズが画像の枠になる。

python-pptx では 1 つの段落が複数の run に分割されていることが多く、
{{price}} が {{pri と ce}} に割れて素朴な置換では当たらない。
段落単位でテキストを結合してから置換することでこれを避けている。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.util import Emu

from app.core.config import get_settings
from app.services.extraction import fields as field_defs

logger = logging.getLogger(__name__)

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
IMAGE_FRAME_RE = re.compile(r"^IMG:([A-Za-z0-9_]+)$")

NULL_PLACEHOLDER = "―"
TSUBO_PER_SQM = 3.305785


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# 値の整形
# --------------------------------------------------------------------------

def format_yen(value: int) -> str:
    """金額を業界の表記に合わせる。

    1億円を「10,000万円」と書く資料は無いので、億が立つときは億で表す。
    万で割り切れない端数がある物件（競売・持分など）は丸めずに円で出す。
    資料に載る数字なので、見やすさより正確さを優先する。
    """
    if value % 10_000 != 0 or value < 10_000_000:
        return f"{value:,}円"
    if value >= 100_000_000:
        oku, remainder = divmod(value, 100_000_000)
        man = remainder // 10_000
        return f"{oku:,}億{man:,}万円" if man else f"{oku:,}億円"
    return f"{value // 10_000:,}万円"


def format_value(value: Any, spec: dict) -> str:
    """DB の生の値を資料に載せる表記へ。

    単位はテンプレートに書かずここで付ける。テンプレートを差し替えても
    表記が揺れない。
    """
    if value is None or value == "":
        return NULL_PLACEHOLDER

    unit = spec.get("unit")
    ftype = spec.get("type")

    if ftype == "integer" and unit == "円":
        return format_yen(value)
    if ftype == "integer" and unit and unit.startswith("円"):
        return f"{value:,}円{unit[1:]}"
    if ftype == "number" and unit == "㎡":
        return f"{value:,.2f}㎡（{value / TSUBO_PER_SQM:,.2f}坪）"
    if ftype == "number" and unit == "%":
        return f"{value:.2f}%"
    if ftype == "date_ym" and isinstance(value, str) and len(value) == 7:
        return f"{value[:4]}年{int(value[5:]):d}月"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def format_station(station: dict) -> str:
    parts = [station.get("line") or ""]
    if station.get("station"):
        parts.append(f"{station['station']}駅")
    if station.get("bus_minutes"):
        parts.append(f"バス{station['bus_minutes']}分")
    if station.get("walk_minutes"):
        parts.append(f"徒歩{station['walk_minutes']}分")
    text = " ".join(part for part in parts if part.strip())
    return text or NULL_PLACEHOLDER


def build_context(data: dict[str, Any], mark_review: bool = True) -> dict[str, str]:
    """{{key}} → 差し込む文字列 の対応表。"""
    specs = field_defs.field_specs()
    fields = data.get("fields") or {}
    context: dict[str, str] = {}

    for key, spec in specs.items():
        name = spec.get("pptx")
        if not name:
            continue
        envelope = fields.get(key) or {}
        text = format_value(envelope.get("value"), spec)
        # 確認前の値がそのまま客先に出るのを防ぐ保険。印刷しても目に入る。
        if mark_review and envelope.get("needs_review") and envelope.get("value") is not None:
            text = f"{text} ※要確認"
        context[name] = text

    stations = data.get("stations") or []
    for index, name in enumerate(field_defs.station_pptx_keys()):
        context[name] = format_station(stations[index]) if index < len(stations) else ""

    for key, value in (data.get("meta") or {}).items():
        if isinstance(value, (str, int, float)) or value is None:
            context[f"meta.{key}"] = "" if value is None else str(value)

    return context


# --------------------------------------------------------------------------
# テキスト差し込み
# --------------------------------------------------------------------------

def _replace_in_text_frame(text_frame, context: dict[str, str], missing: set[str]) -> None:
    """段落単位で置換する。

    run をまたいだプレースホルダに対応するため、段落のテキストを結合して
    置換し、結果を先頭 run に書き戻して残りの run を空にする。書式は
    先頭 run のものに揃う。
    """
    for paragraph in text_frame.paragraphs:
        runs = paragraph.runs
        if not runs:
            continue
        original = "".join(run.text for run in runs)
        if "{{" not in original:
            continue

        def substitute(match: re.Match) -> str:
            key = match.group(1)
            if key in context:
                return context[key]
            missing.add(key)
            return match.group(0)

        replaced = PLACEHOLDER_RE.sub(substitute, original)
        if replaced == original:
            continue
        runs[0].text = replaced
        for run in runs[1:]:
            run.text = ""


def _walk_shapes(shapes) -> Iterable:
    """グループ図形の中身まで再帰的に辿る。"""
    for shape in shapes:
        yield shape
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk_shapes(shape.shapes)


def fill_text(presentation, context: dict[str, str]) -> set[str]:
    """物件概要は表で組まれていることがほとんどなので、表を必ず走査する。"""
    missing: set[str] = set()
    for slide in presentation.slides:
        for shape in _walk_shapes(slide.shapes):
            if shape.has_text_frame:
                _replace_in_text_frame(shape.text_frame, context, missing)
            if shape.has_table:
                for row in shape.table.rows:
                    for cell in row.cells:
                        _replace_in_text_frame(cell.text_frame, context, missing)
        if slide.has_notes_slide:
            _replace_in_text_frame(slide.notes_slide.notes_text_frame, context, missing)
    return missing


# --------------------------------------------------------------------------
# 画像差し込み
# --------------------------------------------------------------------------

def prepare_image(path: Path) -> tuple[io.BytesIO, int, int]:
    """EXIF の向きを反映し、長辺を上限まで縮めたバイト列を返す。

    スマホ撮影の写真はそのまま貼ると横倒しになる。また元画像は 1 枚数 MB
    あることが多く、8 枚貼ると pptx がメール添付できない大きさになる。
    """
    settings = get_settings()
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened)
        limit = settings.pptx_image_max_px
        if max(image.size) > limit:
            image.thumbnail((limit, limit), Image.LANCZOS)

        buffer = io.BytesIO()
        if image.mode in ("RGBA", "LA", "P"):
            image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        else:
            image.convert("RGB").save(
                buffer, format="JPEG", quality=settings.pptx_image_jpeg_quality, optimize=True
            )
        width, height = image.size
    buffer.seek(0)
    return buffer, width, height


def fit_box(frame: tuple[int, int, int, int], px_w: int, px_h: int) -> tuple[int, int, int, int]:
    """枠内にアスペクト比を保って収め、中央寄せした位置とサイズ（EMU）。

    間取り図を引き伸ばすと寸法が狂って見えるので cover は使わない。
    """
    left, top, box_w, box_h = frame
    scale = min(box_w / px_w, box_h / px_h)
    width, height = int(px_w * scale), int(px_h * scale)
    return left + (box_w - width) // 2, top + (box_h - height) // 2, width, height


def fill_images(presentation, images: list[dict],
                drop_empty: bool = False) -> tuple[list[str], list[str]]:
    """名前が IMG:<role> の図形を、対応する画像に置き換える。

    対応する画像が無い枠は既定では残す。テンプレート側で「写真準備中」の
    体裁を作り込んでいることが多く、消すとレイアウトが崩れるため。
    """
    by_role: dict[str, list[dict]] = {}
    for item in images:
        by_role.setdefault(item.get("role", "other"), []).append(item)

    placed: list[str] = []
    empty_frames: list[str] = []

    for slide in presentation.slides:
        for shape in list(_walk_shapes(slide.shapes)):
            match = IMAGE_FRAME_RE.match(shape.name or "")
            if not match:
                continue

            queue = by_role.get(match.group(1), [])
            source = None
            while queue and source is None:
                item = queue.pop(0)
                candidate = Path(item.get("storage_path") or item.get("file", ""))
                if candidate.exists():
                    source = candidate
                else:
                    empty_frames.append(f"{shape.name}（{candidate.name} が見つからない）")

            if source is None:
                empty_frames.append(shape.name)
                if drop_empty:
                    shape._element.getparent().remove(shape._element)
                continue

            frame = (shape.left, shape.top, shape.width, shape.height)
            buffer, px_w, px_h = prepare_image(source)
            left, top, width, height = fit_box(frame, px_w, px_h)
            slide.shapes.add_picture(buffer, Emu(left), Emu(top), Emu(width), Emu(height))
            shape._element.getparent().remove(shape._element)
            placed.append(f"{shape.name} <- {source.name}")

    return placed, empty_frames


# --------------------------------------------------------------------------

def data_hash(data: dict[str, Any]) -> str:
    """生成時のデータの指紋。変わっていないのに再生成した、を検出する。"""
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def render(data: dict[str, Any], template_key: str, output_dir: Path | None = None,
           mark_review: bool = True, drop_empty_frames: bool = False) -> dict[str, Any]:
    """1 物件・1 テンプレート分の pptx を生成する。"""
    settings = get_settings()
    template = settings.template_paths.get(template_key)
    if template is None:
        raise RenderError(f"未知のテンプレートです: {template_key}")
    if not template.exists():
        raise RenderError(f"テンプレートが見つかりません: {template}")

    presentation = Presentation(str(template))
    missing = fill_text(presentation, build_context(data, mark_review))
    placed, empty_frames = fill_images(
        presentation, data.get("images") or [], drop_empty_frames
    )

    name = (data.get("fields", {}).get("property_name") or {}).get("value") or "物件"
    safe_name = re.sub(r'[\\/:*?"<>|]', "_", str(name))
    suffix = "_要確認" if (data.get("meta") or {}).get("review_status") == "要確認" else ""
    target_dir = output_dir or settings.pptx_output_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{safe_name}_{template_key}{suffix}.pptx"
    presentation.save(str(path))

    if missing:
        # 放置すると {{proprety_name}} がそのまま印刷された資料が客先に出る。
        logger.warning("未定義のプレースホルダ: %s", ", ".join(sorted(missing)))

    return {
        "path": str(path),
        "template_key": template_key,
        "data_hash": data_hash(data),
        "placed_images": placed,
        "empty_frames": empty_frames,
        "missing_placeholders": sorted(missing),
    }


def to_pdf(pptx_path: Path) -> Path:
    """営業が実際に配るのは PDF であることが多い。

    コンテナに fonts-noto-cjk が入っていないと全部豆腐になる。
    """
    result = subprocess.run(
        ["soffice", "--headless", "--convert-to", "pdf",
         "--outdir", str(pptx_path.parent), str(pptx_path)],
        capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RenderError(f"PDF 変換に失敗しました: {result.stderr.strip()}")
    return pptx_path.with_suffix(".pdf")
