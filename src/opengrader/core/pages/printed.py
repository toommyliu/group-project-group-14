from __future__ import annotations

import logging
import os
import re
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image

_engine = None
_engine_lock = threading.Lock()
_threads = -1

FIELD_LABEL_RE = re.compile(r"\b(name|student\s*id|id)\b\s*:?", re.IGNORECASE)
FIELD_LINE_RE = re.compile(r"^\s*(name|student\s*id|id)\b\s*(?::|$)", re.IGNORECASE)
_MERGE_GAP = 0.06


@dataclass(frozen=True)
class TextLine:
    text: str
    box: tuple[float, float, float, float]
    score: float = 1.0

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def center_y(self) -> float:
        return (self.box[1] + self.box[3]) / 2


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr import RapidOCR

            logging.getLogger("RapidOCR").setLevel(logging.ERROR)
            _engine = RapidOCR(params={
                "Global.log_level": "error",
                "EngineConfig.onnxruntime.intra_op_num_threads": _threads,
                "EngineConfig.onnxruntime.inter_op_num_threads": _threads,
            })
    return _engine


def limit_threads(threads: int) -> None:
    """Threads per onnxruntime session for the printed-text reader (-1 = all).

    Takes effect on the next read: an engine built with another count is
    dropped, since its sessions keep the thread pools they were made with."""
    global _engine, _threads
    with _engine_lock:
        if threads != _threads:
            _engine = None
        _threads = threads


def app_thread_budget(cpus: int | None = None) -> int:
    """Threads per reader session inside an interactive app (TUI).

    onnxruntime's default is one spinning thread per physical core for *each*
    of the reader's sessions; run in the app's own process (Launch Grading's
    split) that was 40+ threads at 400–700 % CPU on a 16-thread Mac and the
    screen lagged. A quarter of the CPUs, at most 4, keeps the read fast and
    leaves the rest of the machine to the user."""
    cpus = cpus or os.cpu_count() or 1
    return max(1, min(4, cpus // 4))


def _ocr(image: Image.Image, *, detect: bool):
    engine = _get_engine()
    with _engine_lock:
        return engine(np.ascontiguousarray(np.asarray(image.convert("RGB"))), use_det=detect, use_cls=False, use_rec=True,
                      return_word_box=detect, return_single_char_box=False, box_thresh=0.3, text_score=0.5, unclip_ratio=1.6)


def read_lines(image: Image.Image) -> list[TextLine]:
    width, height = image.size
    result = _ocr(image, detect=True)
    words = result.word_results or ()
    fragments: list[TextLine] = []
    spans: list[list[tuple[int, float]]] = []
    for index, (box, text, score) in enumerate(zip(result.boxes if result.boxes is not None else [], result.txts or (), result.scores or ())):
        xs, ys = box[:, 0] / width, box[:, 1] / height
        fragments.append(TextLine(str(text), (float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())), float(score)))
        spans.append([(start, x0 / width) for start, _end, (x0, _y0, _x1, _y1) in _words(str(text), words[index] if index < len(words) else ())])
    return [_label_only(line) for line in merge_lines(split_field_labels(fragments, spans))]


def _words(text: str, words) -> list[tuple[int, int, tuple[float, float, float, float]]]:
    spans, position = [], 0
    for word, _score, box in words:
        offset = text.find(str(word), position)
        if offset < 0:
            return []
        xs, ys = [point[0] for point in box], [point[1] for point in box]
        spans.append((offset, offset + len(str(word)), (min(xs), min(ys), max(xs), max(ys))))
        position = offset + len(str(word))
    return spans


def split_field_labels(fragments: list[TextLine], word_starts: list[list[tuple[int, float]]] | None = None) -> list[TextLine]:
    out: list[TextLine] = []
    for index, line in enumerate(fragments):
        cuts = [m.start() for m in FIELD_LABEL_RE.finditer(line.text) if m.start() > 0 and _is_label_at(line.text, m)]
        if not cuts:
            out.append(line)
            continue
        x0, y0, x1, y1 = line.box
        known = dict(word_starts[index]) if word_starts and index < len(word_starts) else {}
        at = lambda char: x0 if char == 0 else x1 if char == len(line.text) else known.get(char, x0 + (x1 - x0) * char / len(line.text))
        bounds = [0, *cuts, len(line.text)]
        for a, b in zip(bounds, bounds[1:]):
            piece = line.text[a:b].strip()
            if piece:
                out.append(TextLine(piece, (at(a), y0, at(b), y1), line.score))
    return out


def _label_only(line: TextLine) -> TextLine:
    match = FIELD_LINE_RE.match(line.text)
    if not match or match.end() >= len(line.text.rstrip()):
        return line
    x0, y0, x1, y1 = line.box
    return TextLine(match.group(0).strip(), (x0, y0, x0 + (x1 - x0) * match.end() / len(line.text), y1), line.score)


def _is_label_at(text: str, match: re.Match) -> bool:
    return text[match.start():match.end()].rstrip().endswith(":")


def merge_lines(fragments: list[TextLine]) -> list[TextLine]:
    rows: list[list[TextLine]] = []
    for fragment in sorted(fragments, key=lambda f: f.center_y):
        for row in rows:
            if abs(fragment.center_y - row[0].center_y) < 0.5 * min(fragment.height, row[0].height):
                row.append(fragment)
                break
        else:
            rows.append([fragment])
    lines: list[TextLine] = []
    for row in rows:
        row.sort(key=lambda f: f.box[0])
        chunk = [row[0]]
        for fragment in row[1:]:
            starts_label = FIELD_LINE_RE.match(fragment.text) is not None
            taller = max(fragment.height, chunk[-1].height) / max(1e-6, min(fragment.height, chunk[-1].height))
            if (fragment.box[0] - chunk[-1].box[2] > _MERGE_GAP or starts_label or taller > 1.4
                    or FIELD_LABEL_RE.fullmatch(chunk[-1].text.strip())):
                lines.append(_join(chunk))
                chunk = [fragment]
            else:
                chunk.append(fragment)
        lines.append(_join(chunk))
    return sorted(lines, key=lambda line: (line.box[1], line.box[0]))


def _join(chunk: list[TextLine]) -> TextLine:
    return TextLine(
        " ".join(f.text for f in chunk),
        (min(f.box[0] for f in chunk), min(f.box[1] for f in chunk), max(f.box[2] for f in chunk), max(f.box[3] for f in chunk)),
        min(f.score for f in chunk),
    )


def read_title(image: Image.Image) -> str:
    starts = subscript_starts(np.asarray(image.convert("L")))
    bounds = [0, *starts, image.width]
    pieces = [recognize_line(image.crop((a, 0, b, image.height))) for a, b in zip(bounds, bounds[1:])]
    if len(pieces) > 1 and not all(pieces):
        pieces = [recognize_line(image)]
    text = "_".join(pieces)
    cut = next((m.start() for m in FIELD_LABEL_RE.finditer(text) if m.start() > 0 and _is_label_at(text, m)), len(text))
    return text[:cut].strip()


def recognize_line(image: Image.Image) -> str:
    return " ".join(str(text) for text in (_ocr(image, detect=False).txts or ())).strip()


def subscript_starts(gray: np.ndarray) -> list[int]:
    import cv2

    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    _, _, stats, _ = cv2.connectedComponentsWithStats(ink, connectivity=8)
    speck = 0.08 * gray.shape[0]
    columns: list[list[int]] = []
    for x, y, w, h, _area in sorted(stats[1:].tolist()):
        if max(w, h) < speck or (w > 4 * h and w > 2 * speck):
            continue
        if columns and x < columns[-1][2] - 1:
            column = columns[-1]
            columns[-1] = [column[0], min(column[1], y), max(column[2], x + w), max(column[3], y + h)]
        else:
            columns.append([x, y, x + w, y + h])
    if len(columns) < 4:
        return []

    centres = np.array([(c[0] + c[2]) / 2 for c in columns], dtype=float)
    tops = np.array([c[1] for c in columns], dtype=float)
    bottoms = np.array([c[3] for c in columns], dtype=float)
    heights = bottoms - tops
    pairs = [(bottoms[j] - bottoms[i]) / (centres[j] - centres[i])
             for i in range(len(columns)) for j in range(i + 1, len(columns)) if centres[j] - centres[i] > 0]
    slope = float(np.median(pairs)) if pairs else 0.0
    baseline = float(np.median(bottoms - slope * centres)) + slope * centres
    below = bottoms - baseline
    on_line = np.abs(below) <= 0.15 * float(np.median(heights))
    if on_line.sum() < 3:
        return []
    cap = float(np.percentile(heights[on_line], 75))
    drop = tops - (baseline - cap)
    small = (below > 0.1 * cap) & (drop > 0.45 * cap) & (heights < 0.8 * cap)

    starts = []
    index = 1
    while index < len(columns):
        if not small[index] or small[index - 1]:
            index += 1
            continue
        end = index
        while end + 1 < len(columns) and small[end + 1]:
            end += 1
        base = index - 1
        attached = columns[index][0] - columns[base][2] < 0.4 * cap
        if on_line[base] and heights[base] >= 0.6 * cap and attached and heights[index:end + 1].max() >= 0.5 * cap:
            starts.append(int(columns[index][0]))
        index = end + 1
    return starts


_DETECT_SIDE = 736


def read_field(
    image: Image.Image,
    kind: str,
    printed: tuple[tuple[float, float, float, float], ...] = (),
    written_width: float = 1.0,
) -> tuple[str, float | None]:
    canvas = Image.new("RGB", (max(image.width, _DETECT_SIDE), max(image.height, _DETECT_SIDE)), "white")
    canvas.paste(image.convert("RGB"), (0, 0))
    result = _ocr(canvas, detect=True)
    stop = written_width * image.width - 3
    on_print = [(x0 * image.width, y0 * image.height, x1 * image.width, y1 * image.height) for x0, y0, x1, y1 in printed]
    words = result.word_results or ()
    pieces = []
    for index, (box, text, score) in enumerate(zip(result.boxes if result.boxes is not None else [], result.txts or (), result.scores or ())):
        text = str(text)
        spans = _words(text, words[index] if index < len(words) else ()) or [
            (0, len(text), (float(box[:, 0].min()), float(box[:, 1].min()), float(box[:, 0].max()), float(box[:, 1].max())))]
        kept = [(x0, text[start:end]) for start, end, (x0, y0, x1, y1) in spans
                if x0 < stop and not any(a <= (x0 + x1) / 2 <= c and b <= (y0 + y1) / 2 <= d for a, b, c, d in on_print)]
        piece = " ".join(word for _, word in kept).strip()
        if (match := FIELD_LINE_RE.match(piece)) or (match := FIELD_LABEL_RE.match(piece)):
            piece = piece[match.end():].strip()
        if piece:
            pieces.append((kept[0][0], piece, float(score)))
    pieces.sort()
    text = " ".join(piece for _, piece, _ in pieces)
    text = re.sub(r"\D", "", text) if kind == "sid" else re.sub(r"^[\W_]+|[\W_]+$", "", text)
    confidence = 100 * min(score for _, _, score in pieces) if pieces and text else None
    return text, confidence
