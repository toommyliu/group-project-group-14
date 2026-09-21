"""CER computation and the adjudication table for the Phase 0 spike.

Design constraints this file exists to satisfy (see README.md):

- The adjudication table (`build_adjudication_rows` / `render_table`) is the
  PRIMARY output. It is useful with an empty gold.toml, on day one, because
  it puts every backend's transcription of every crop side by side for a
  human to read. It does not require a reference transcription.
- CER (`character_error_rate`) is SECONDARY. It is only ever computed against
  a human-verified `gold.toml` entry, never against the incumbent's own
  recorded `Response text`. A crop with no gold entry is excluded from CER,
  not silently scored against something that isn't ground truth.
- `n` is reported everywhere a mean is reported. A mean CER over a handful of
  crops, printed without n, invites the reader to trust it like a real
  benchmark. Print the per-crop numbers too.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def levenshtein_distance(a: str, b: str) -> int:
    """Classic O(len(a)*len(b)) edit distance, iterative, two-row DP.

    No third-party library — this file has no dependency beyond the
    standard library, matching the rest of the spike (§7.8 Q4: raw
    transport only, no new dependency).
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    previous = list(range(len(b) + 1))
    current = [0] * (len(b) + 1)

    for i, ca in enumerate(a, start=1):
        current[0] = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                previous[j] + 1,  # deletion
                current[j - 1] + 1,  # insertion
                previous[j - 1] + cost,  # substitution
            )
        previous, current = current, previous

    return previous[len(b)]


def character_error_rate(hypothesis: str, reference: str) -> float | None:
    """Normalised Levenshtein distance / len(reference).

    Returns None if the reference is empty — CER is undefined there, and
    reporting 0.0 or infinity would both be misleading rather than absent.
    """
    if not reference:
        return None
    return levenshtein_distance(hypothesis, reference) / len(reference)


@dataclass
class BackendCropResult:
    """One backend's outcome for one crop. Produced by run_spike.py."""

    backend: str
    crop_id: str
    text: str | None
    """Transcribed text, or None if the call failed / was not attempted."""
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


@dataclass
class AdjudicationRow:
    crop_id: str
    incumbent_read_by: str | None
    incumbent_text: str | None
    """Labeled ollama-incumbent-recorded in all rendering — never 'reference'."""
    gold_text: str | None
    """Human-verified reference from gold.toml, or None if not yet filled in."""
    backend_texts: dict[str, str | None] = field(default_factory=dict)
    disagreement: bool = False


def build_adjudication_rows(
    crops_by_id: dict[str, "object"],
    results: list[BackendCropResult],
    gold: dict[str, dict],
) -> list[AdjudicationRow]:
    """Assemble one row per crop with every backend's text side by side."""
    by_crop: dict[str, dict[str, BackendCropResult]] = {}
    for r in results:
        by_crop.setdefault(r.crop_id, {})[r.backend] = r

    rows: list[AdjudicationRow] = []
    for crop_id, crop in crops_by_id.items():
        backend_results = by_crop.get(crop_id, {})
        backend_texts = {name: res.text for name, res in backend_results.items()}

        distinct = {t.strip() for t in backend_texts.values() if t is not None}
        disagreement = len(distinct) > 1

        gold_entry = gold.get(crop_id)
        gold_text = gold_entry.get("gold_text") if gold_entry else None
        # An empty-string stub (not yet filled in) does not count as a gold entry.
        if gold_text is not None and gold_text.strip() == "":
            gold_text = None

        rows.append(
            AdjudicationRow(
                crop_id=crop_id,
                incumbent_read_by=getattr(crop, "read_by", None),
                incumbent_text=getattr(crop, "incumbent_response_text", None),
                gold_text=gold_text,
                backend_texts=backend_texts,
                disagreement=disagreement,
            )
        )
    return rows


def _truncate(text: str | None, width: int) -> str:
    if text is None:
        return "(no call)"
    flat = " ".join(text.split())
    if len(flat) <= width:
        return flat
    return flat[: width - 1] + "…"


def render_table(rows: list[AdjudicationRow], backend_names: list[str], *, width: int = 40) -> str:
    """Human-readable adjudication table: one crop per row, backends side by side."""
    lines: list[str] = []
    header = ["crop", "incumbent (ollama-incumbent-recorded)"] + list(backend_names) + ["gold", "disagree?"]
    lines.append(" | ".join(header))
    lines.append("-" * (len(" | ".join(header))))
    for row in rows:
        cells = [
            row.crop_id,
            f"[{row.incumbent_read_by or '?'}] {_truncate(row.incumbent_text, width)}",
        ]
        for name in backend_names:
            cells.append(_truncate(row.backend_texts.get(name), width))
        cells.append(_truncate(row.gold_text, width) if row.gold_text else "(no gold entry — excluded from CER)")
        cells.append("YES" if row.disagreement else "")
        lines.append(" | ".join(cells))
    return "\n".join(lines)


@dataclass
class CerSummary:
    backend: str
    n: int
    per_crop: list[tuple[str, float]]
    """(crop_id, cer) pairs actually scored — the honest unit, not just the mean."""
    mean: float | None


def compute_cer_summary(
    rows: list[AdjudicationRow], backend_names: list[str]
) -> list[CerSummary]:
    """CER per backend, over ONLY crops that have a gold entry. Always carries n."""
    summaries: list[CerSummary] = []
    for name in backend_names:
        per_crop: list[tuple[str, float]] = []
        for row in rows:
            if row.gold_text is None:
                continue
            hyp = row.backend_texts.get(name)
            if hyp is None:
                continue
            cer = character_error_rate(hyp, row.gold_text)
            if cer is None:
                continue
            per_crop.append((row.crop_id, cer))
        mean = sum(c for _, c in per_crop) / len(per_crop) if per_crop else None
        summaries.append(CerSummary(backend=name, n=len(per_crop), per_crop=per_crop, mean=mean))
    return summaries


def render_cer_summary(summaries: list[CerSummary]) -> str:
    lines = []
    for s in summaries:
        if s.n == 0:
            lines.append(f"{s.backend}: n=0 (no crops with a gold entry were transcribed by this backend — CER not computable)")
            continue
        lines.append(f"{s.backend}: n={s.n} mean CER={s.mean:.4f}")
        for crop_id, cer in s.per_crop:
            lines.append(f"    {crop_id}: CER={cer:.4f}")
    return "\n".join(lines)
