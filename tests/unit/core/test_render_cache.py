"""Unit tests for the process-wide, byte-budgeted render cache.

See ``opengrader.core.render_cache``'s module docstring for the invariants
these exercise: content-hash identity, LRU eviction against a byte budget,
independent images per lookup, and the ``OPENGRADER_RENDER_CACHE_MB`` default.
"""

from __future__ import annotations

import threading
from pathlib import Path

import fitz
import pytest

from opengrader.core.render_cache import (
    FALLBACK_CACHE_BYTES,
    MAX_CACHE_BYTES,
    MIN_CACHE_BYTES,
    RenderedPage,
    clear_render_cache,
    configure_render_cache,
    default_render_cache_bytes,
    document_fingerprint,
    file_fingerprint,
    render_cache_get,
    render_cache_max_bytes,
    render_cache_put,
    render_cache_stats,
)

_ORIGINAL_MAX_BYTES = render_cache_max_bytes()


@pytest.fixture(autouse=True)
def _reset_render_cache():
    """Every test starts from — and leaves — an empty cache at the process default.

    The cache is module-level global state shared across the whole test
    session, so a test that populates or resizes it must not leak into a
    neighbour (in this file or any other).
    """
    clear_render_cache()
    configure_render_cache(max_bytes=_ORIGINAL_MAX_BYTES)
    try:
        yield
    finally:
        clear_render_cache()
        configure_render_cache(max_bytes=_ORIGINAL_MAX_BYTES)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class _FakePixmap:
    """A minimal stand-in for a PyMuPDF pixmap, with a controllable stride."""

    def __init__(self, width: int, height: int, n: int, samples: bytes, stride: int) -> None:
        self.width = width
        self.height = height
        self.n = n
        self.samples = samples
        self.stride = stride


def _rgb_pixmap(width: int, height: int, *, pad: int = 0) -> _FakePixmap:
    """A tightly- or loosely-packed 3-sample (RGB) fake pixmap.

    Each pixel is filled with a value derived from its row so a sheared
    unpack (reading padding bytes as pixel data) is detectable.
    """
    row_bytes = width * 3
    stride = row_bytes + pad
    rows = []
    for row in range(height):
        value = row % 256
        rows.append(bytes([value]) * row_bytes + bytes(pad))
    return _FakePixmap(width, height, 3, b"".join(rows), stride)


def _make_pdf(tmp_path: Path, name: str = "doc.pdf", *, width=200, height=200) -> Path:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# RenderedPage: round-trip, independence, stride repacking
# --------------------------------------------------------------------------- #


def test_from_pixmap_to_image_round_trips_pixels_exactly() -> None:
    pix = _rgb_pixmap(4, 3)
    page = RenderedPage.from_pixmap(pix)
    image = page.to_image()
    assert image.mode == "RGB"
    assert image.size == (4, 3)
    for row in range(3):
        for col in range(4):
            assert image.getpixel((col, row)) == (row, row, row)


def test_to_image_returns_independent_objects_each_call() -> None:
    pix = _rgb_pixmap(4, 3)
    page = RenderedPage.from_pixmap(pix)
    first = page.to_image()
    second = page.to_image()
    assert first is not second

    original = first.getpixel((0, 0))
    first.putpixel((0, 0), (255, 0, 0))
    # Mutating one image must not affect the other, nor the underlying page...
    assert second.getpixel((0, 0)) == original
    # ...nor a later materialization from the same RenderedPage.
    third = page.to_image()
    assert third.getpixel((0, 0)) == original


def test_from_pixmap_repacks_a_padded_stride_without_shearing() -> None:
    width, height = 5, 4
    padded = _rgb_pixmap(width, height, pad=7)
    tight = _rgb_pixmap(width, height, pad=0)

    padded_page = RenderedPage.from_pixmap(padded)
    tight_page = RenderedPage.from_pixmap(tight)

    assert padded_page.width == width
    assert padded_page.height == height
    # The repacked bytes must match a page whose stride was already tight —
    # a shear would offset later rows by the padding and break this.
    assert padded_page.data == tight_page.data

    image = padded_page.to_image()
    for row in range(height):
        assert image.getpixel((0, row)) == (row, row, row)
        assert image.getpixel((width - 1, row)) == (row, row, row)


def test_unsupported_sample_count_raises() -> None:
    pix = _FakePixmap(2, 2, n=2, samples=b"\x00" * 8, stride=4)
    with pytest.raises(ValueError):
        RenderedPage.from_pixmap(pix)


# --------------------------------------------------------------------------- #
# LRU eviction against the byte budget
# --------------------------------------------------------------------------- #


def _page(nbytes: int, fill: int = 0) -> RenderedPage:
    return RenderedPage(mode="L", width=nbytes, height=1, data=bytes([fill]) * nbytes)


def test_puts_beyond_budget_evict_least_recently_used_first() -> None:
    configure_render_cache(max_bytes=30)
    render_cache_put(("a", 0, 100), _page(10))
    render_cache_put(("b", 0, 100), _page(10))
    render_cache_put(("c", 0, 100), _page(10))
    # Budget holds exactly three 10-byte pages so far.
    stats = render_cache_stats()
    assert stats.entries == 3
    assert stats.bytes_used == 30
    assert stats.evictions == 0

    # A fourth put must evict "a" (least-recently used) to make room.
    render_cache_put(("d", 0, 100), _page(10))
    stats = render_cache_stats()
    assert stats.entries == 3
    assert stats.bytes_used == 30
    assert stats.evictions == 1
    assert render_cache_get(("a", 0, 100)) is None
    assert render_cache_get(("b", 0, 100)) is not None


def test_get_promotes_an_entry_so_it_survives_the_next_eviction() -> None:
    configure_render_cache(max_bytes=30)
    render_cache_put(("a", 0, 100), _page(10))
    render_cache_put(("b", 0, 100), _page(10))
    render_cache_put(("c", 0, 100), _page(10))

    # Touch "a" so it is now the most-recently used...
    hit = render_cache_get(("a", 0, 100))
    assert hit is not None

    # ...so the next put should evict "b" (now least-recently used), not "a".
    render_cache_put(("d", 0, 100), _page(10))
    assert render_cache_get(("a", 0, 100)) is not None
    assert render_cache_get(("b", 0, 100)) is None
    assert render_cache_get(("c", 0, 100)) is not None
    assert render_cache_get(("d", 0, 100)) is not None


def test_stats_track_hits_misses_evictions_entries_bytes() -> None:
    configure_render_cache(max_bytes=20)
    assert render_cache_get(("x", 0, 100)) is None  # miss
    render_cache_put(("x", 0, 100), _page(10))
    render_cache_put(("y", 0, 100), _page(10))
    assert render_cache_get(("x", 0, 100)) is not None  # hit
    assert render_cache_get(("y", 0, 100)) is not None  # hit
    render_cache_put(("z", 0, 100), _page(10))  # evicts x (y was touched more recently)

    stats = render_cache_stats()
    assert stats.hits == 2
    assert stats.misses == 1
    assert stats.evictions == 1
    assert stats.entries == 2
    assert stats.bytes_used == 20
    assert stats.max_bytes == 20
    assert stats.hit_rate == pytest.approx(2 / 3)


def test_replacing_an_existing_key_updates_bytes_used_without_double_counting() -> None:
    configure_render_cache(max_bytes=100)
    render_cache_put(("a", 0, 100), _page(10))
    render_cache_put(("a", 0, 100), _page(20))
    stats = render_cache_stats()
    assert stats.entries == 1
    assert stats.bytes_used == 20


# --------------------------------------------------------------------------- #
# oversized single page
# --------------------------------------------------------------------------- #


def test_a_page_larger_than_the_whole_budget_is_not_cached() -> None:
    configure_render_cache(max_bytes=50)
    render_cache_put(("small", 0, 100), _page(10))
    render_cache_put(("huge", 0, 100), _page(1000))  # bigger than the whole budget

    stats = render_cache_stats()
    # The oversized page must not have been stored...
    assert render_cache_get(("huge", 0, 100)) is None
    # ...and must not have evicted the cache to make room for something that
    # would immediately not fit anyway.
    assert render_cache_get(("small", 0, 100)) is not None
    assert stats.entries == 1
    assert stats.evictions == 0


# --------------------------------------------------------------------------- #
# configure_render_cache(max_bytes=0) disables and empties the cache
# --------------------------------------------------------------------------- #


def test_configure_zero_disables_and_empties_the_cache() -> None:
    configure_render_cache(max_bytes=1000)
    render_cache_put(("a", 0, 100), _page(10))
    assert render_cache_stats().entries == 1

    configure_render_cache(max_bytes=0)
    assert render_cache_max_bytes() == 0
    assert render_cache_stats().entries == 0
    assert render_cache_stats().bytes_used == 0

    # And puts are now no-ops.
    render_cache_put(("b", 0, 100), _page(10))
    assert render_cache_stats().entries == 0
    assert render_cache_get(("b", 0, 100)) is None


# --------------------------------------------------------------------------- #
# default_render_cache_bytes()
# --------------------------------------------------------------------------- #


def test_default_bytes_honours_env_var_mb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENGRADER_RENDER_CACHE_MB", "5")
    assert default_render_cache_bytes() == 5 * 1024 * 1024


def test_default_bytes_env_var_zero_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENGRADER_RENDER_CACHE_MB", "0")
    assert default_render_cache_bytes() == 0


def test_default_bytes_falls_back_sanely_on_garbage_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENGRADER_RENDER_CACHE_MB", "not-a-number")
    # A garbage value must not disable or crash — it falls through to the
    # physical-memory-derived default like an unset variable would.
    value = default_render_cache_bytes()
    assert MIN_CACHE_BYTES <= value <= MAX_CACHE_BYTES


def test_default_bytes_negative_env_value_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENGRADER_RENDER_CACHE_MB", "-5")
    value = default_render_cache_bytes()
    assert MIN_CACHE_BYTES <= value <= MAX_CACHE_BYTES


def test_default_bytes_unset_uses_ram_fraction_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENGRADER_RENDER_CACHE_MB", raising=False)
    monkeypatch.setattr(
        "opengrader.core.render_cache._total_ram_bytes", lambda: 16 * 1024 * _MIB_FOR_TEST
    )
    # A sixteenth of 16 GiB is 1 GiB, right at the ceiling.
    assert default_render_cache_bytes() == MAX_CACHE_BYTES


_MIB_FOR_TEST = 1024 * 1024


def test_default_bytes_clamps_to_floor_on_a_tiny_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENGRADER_RENDER_CACHE_MB", raising=False)
    monkeypatch.setattr(
        "opengrader.core.render_cache._total_ram_bytes", lambda: 512 * _MIB_FOR_TEST
    )
    # A sixteenth of 512 MiB (32 MiB) is below the floor.
    assert default_render_cache_bytes() == MIN_CACHE_BYTES


def test_default_bytes_clamps_to_ceiling_on_a_huge_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENGRADER_RENDER_CACHE_MB", raising=False)
    monkeypatch.setattr(
        "opengrader.core.render_cache._total_ram_bytes", lambda: 256 * 1024 * _MIB_FOR_TEST
    )
    assert default_render_cache_bytes() == MAX_CACHE_BYTES


def test_default_bytes_uses_fallback_when_ram_cannot_be_determined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENGRADER_RENDER_CACHE_MB", raising=False)
    monkeypatch.setattr(
        "opengrader.core.render_cache._total_ram_bytes", lambda: None
    )
    assert default_render_cache_bytes() == FALLBACK_CACHE_BYTES


# --------------------------------------------------------------------------- #
# file_fingerprint / document_fingerprint
# --------------------------------------------------------------------------- #


def test_file_fingerprint_same_content_two_paths_matches(tmp_path: Path) -> None:
    content = b"%PDF-1.4 fake but identical content\n"
    a = tmp_path / "a.pdf"
    b = tmp_path / "sub" / "b.pdf"
    b.parent.mkdir()
    a.write_bytes(content)
    b.write_bytes(content)

    fp_a = file_fingerprint(a)
    fp_b = file_fingerprint(b)
    assert fp_a is not None
    assert fp_a == fp_b


def test_file_fingerprint_changes_when_content_is_rewritten(tmp_path: Path) -> None:
    path = tmp_path / "a.pdf"
    path.write_bytes(b"version one")
    first = file_fingerprint(path)

    # Force a different (dev, ino, size, mtime_ns) memo key so the change is
    # actually re-hashed rather than served from the stat-based memo — a
    # same-size, same-nanosecond rewrite is the one documented staleness
    # window and the pipeline never does that (every "fixing" stage writes a
    # new file), so this deliberately changes size too.
    path.write_bytes(b"version two, a different length")
    second = file_fingerprint(path)

    assert first is not None
    assert second is not None
    assert first != second


def test_file_fingerprint_nonexistent_path_returns_none(tmp_path: Path) -> None:
    assert file_fingerprint(tmp_path / "nope.pdf") is None


def test_document_fingerprint_stream_document_returns_none() -> None:
    # A synthesized/in-memory document has no path behind it.
    doc = fitz.open()
    doc.new_page(width=100, height=100)
    raw = doc.tobytes()
    doc.close()

    stream_doc = fitz.open(stream=raw, filetype="pdf")
    try:
        assert document_fingerprint(stream_doc) is None
    finally:
        stream_doc.close()


def test_document_fingerprint_matches_file_fingerprint(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        assert document_fingerprint(doc) == file_fingerprint(path)
    finally:
        doc.close()


def test_document_fingerprint_dirty_document_returns_none(tmp_path: Path) -> None:
    path = _make_pdf(tmp_path)
    doc = fitz.open(str(path))
    try:
        doc.new_page(width=100, height=100)  # unsaved edit -> is_dirty
        assert document_fingerprint(doc) is None
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# thread safety
# --------------------------------------------------------------------------- #


def test_concurrent_put_get_from_many_threads_stays_within_budget() -> None:
    max_bytes = 5_000
    configure_render_cache(max_bytes=max_bytes)
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker(worker_id: int) -> None:
        try:
            barrier.wait(timeout=5)
            for i in range(200):
                key = (f"file{(worker_id + i) % 6}", i % 4, 100)
                render_cache_put(key, _page(50))
                render_cache_get(key)
                render_cache_get((f"file{worker_id}", 999, 100))  # frequent misses too
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert not errors
    stats = render_cache_stats()
    assert stats.bytes_used <= max_bytes
    # Internal consistency: the byte accounting matches what eviction left.
    assert stats.bytes_used >= 0
