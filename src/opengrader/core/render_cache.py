"""One raster per page, shared across every stage of a run.

``scan-check``, boundary detection, reorder, QR reading and the extractor each
open the same PDF and rasterize the same pages independently, so a single page
is commonly rendered 3-4× per run. This module is the shared store that turns
those repeats into one render, sitting behind the single seam
:func:`opengrader.core.pdf_utils.render_page_to_image` so no caller changes.

**What is cached is bytes, not a PIL image.** A :class:`RenderedPage` holds the
pixmap's packed samples, and every lookup materializes a *fresh*
``Image.Image`` from them. That costs a few milliseconds against tens for a
re-render, and it buys the property that makes the cache safe to drop under an
existing pipeline: two callers can never alias one image, so a stage that
deskews, crops or draws in place cannot corrupt what the next stage reads. It
also means the cache never has to reason about Pillow's copy-on-convert rules.

**Identity is the file's SHA-256**, not its path: the same content reached by
two paths is one entry, and a rewritten file is a different entry rather than a
stale one. Hashing is memoized on ``(dev, ino, size, mtime_ns)``, so a bundle is
digested once per run rather than once per page. The residual staleness window
is a file rewritten with an identical size *and* an identical nanosecond mtime —
which the pipeline never does, because every stage that "fixes" a scan
(``scan_check.apply_rotation_fixes``, the splitter) writes a **new file** on
purpose. Documents with no file behind them (``fitz.open(stream=…)``) have no
identity and are simply not cached.

**Entries are evicted LRU against a byte budget** — a page is 6.3 MB at 150 DPI
and 25 MB at 300 DPI, so an unbounded cache would hold a 200-page bundle in
1.3 GB of RAM. The default budget is a sixteenth of physical memory, clamped to
[128 MiB, 1 GiB], and ``OPENGRADER_RENDER_CACHE_MB`` overrides it (``0``
disables caching entirely). Because the budget is a *cap* and not an
allocation, a run that renders three pages holds three pages.

The one access pattern this cannot help is a bundle whose working set exceeds
the budget: three sequential full passes over N pages with room for K < N is
the classic LRU scan pathology, and every entry is evicted just before it would
have been reused. Raising ``OPENGRADER_RENDER_CACHE_MB`` is the fix; compressing
entries is not — measured on a real 150-DPI scan, zlib-1 costs 30 ms to encode
against the 32 ms the render itself takes, so the compression pays for a second
render before the first reuse.
"""

from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import Image

__all__ = [
    "RenderCacheStats",
    "RenderedPage",
    "clear_render_cache",
    "configure_render_cache",
    "default_render_cache_bytes",
    "document_fingerprint",
    "render_cache_get",
    "render_cache_max_bytes",
    "render_cache_put",
    "render_cache_stats",
]

_MIB = 1024 * 1024

# Budget bounds. The floor keeps the cache useful on a small machine (128 MiB is
# 20 pages at 150 DPI — enough for the extractor's per-submission working set);
# the ceiling keeps a 64 GB workstation from quietly parking a gigabyte and a
# half of scans in RAM for a run that only needed a few.
MIN_CACHE_BYTES = 128 * _MIB
MAX_CACHE_BYTES = 1024 * _MIB
# Used when physical memory cannot be determined.
FALLBACK_CACHE_BYTES = 256 * _MIB

CACHE_BYTES_ENV_VAR = "OPENGRADER_RENDER_CACHE_MB"

# How many distinct files' digests to remember. Bounded so a long-lived TUI
# session that pages through hundreds of submissions cannot grow it without
# limit; a run only ever needs the handful of PDFs it is working on.
_FINGERPRINT_MEMO_SIZE = 512

_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class RenderedPage:
    """A rasterized page as immutable, packed samples.

    Deliberately not a ``PIL.Image``: see the module docstring — handing every
    caller its own image is what makes sharing one render across stages safe.
    """

    mode: str
    width: int
    height: int
    data: bytes

    @property
    def nbytes(self) -> int:
        return len(self.data)

    @classmethod
    def from_pixmap(cls, pix: Any) -> RenderedPage:
        """Capture a PyMuPDF pixmap's samples, repacking a padded stride.

        PyMuPDF packs rows tightly in practice (``stride == width * n``), but
        the API does not promise it, and a padded row would shear the image if
        handed to Pillow as-is.
        """
        mode = _PIXMAP_MODES.get(pix.n)
        if mode is None:
            raise ValueError(f"unsupported pixmap with {pix.n} samples per pixel")
        row_bytes = pix.width * pix.n
        samples = pix.samples
        if pix.stride != row_bytes:
            view = memoryview(samples)
            samples = b"".join(
                bytes(view[row * pix.stride : row * pix.stride + row_bytes])
                for row in range(pix.height)
            )
        return cls(
            mode=mode,
            width=pix.width,
            height=pix.height,
            data=bytes(samples),
        )

    def to_image(self) -> Image.Image:
        """Materialize a fresh, caller-owned image over these samples."""
        from PIL import Image as PILImage

        return PILImage.frombytes(self.mode, (self.width, self.height), self.data)


# ``alpha=False`` on the default RGB colorspace always yields n=3; the other two
# are covered so an explicit-colorspace caller cannot silently get a sheared
# image instead of an error.
_PIXMAP_MODES = {1: "L", 3: "RGB", 4: "CMYK"}


@dataclass(frozen=True)
class RenderCacheStats:
    """A snapshot of cache behaviour, for benchmarks and ``doctor``."""

    hits: int
    misses: int
    evictions: int
    entries: int
    bytes_used: int
    max_bytes: int

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


def _total_ram_bytes() -> int | None:
    """Physical memory, or ``None`` where it cannot be read.

    ``SC_PHYS_PAGES`` is present on both macOS and Linux; the guard is for
    everywhere else (and for a sandbox that reports nonsense).
    """
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return pages * page_size


def default_render_cache_bytes() -> int:
    """The budget a process starts with.

    ``OPENGRADER_RENDER_CACHE_MB`` wins when set and parseable (``0`` disables
    the cache); otherwise a sixteenth of physical memory, clamped.
    """
    raw = os.environ.get(CACHE_BYTES_ENV_VAR)
    if raw is not None and raw.strip():
        # An unparseable or negative value falls through to the RAM-derived
        # default rather than raising: a mistyped tuning knob must not stop a
        # grading run, and the default it falls back to is always safe.
        try:
            megabytes = float(raw)
        except ValueError:
            megabytes = None
        if megabytes is not None and megabytes >= 0.0:
            return int(megabytes * _MIB)
    total = _total_ram_bytes()
    if total is None:
        return FALLBACK_CACHE_BYTES
    return max(MIN_CACHE_BYTES, min(MAX_CACHE_BYTES, total // 16))


class _RenderCache:
    """Byte-budgeted LRU over ``(sha256, page_index, dpi)``.

    One lock guards everything. The critical sections are dictionary
    operations on already-built ``bytes``, never a render or an image
    materialization, so the TUI's render threads and the OCR path contend for
    microseconds.
    """

    def __init__(self, max_bytes: int) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple[str, int, int], RenderedPage] = OrderedDict()
        self._bytes = 0
        self._max_bytes = max(0, max_bytes)
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    @property
    def max_bytes(self) -> int:
        with self._lock:
            return self._max_bytes

    def configure(self, max_bytes: int) -> None:
        with self._lock:
            self._max_bytes = max(0, max_bytes)
            self._evict_locked()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0
            self._hits = 0
            self._misses = 0
            self._evictions = 0

    def get(self, key: tuple[str, int, int]) -> RenderedPage | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry

    def put(self, key: tuple[str, int, int], page: RenderedPage) -> None:
        with self._lock:
            if self._max_bytes <= 0:
                return
            # A page that cannot fit on its own would evict the entire cache to
            # store something nothing can reuse before the next put drops it.
            if page.nbytes > self._max_bytes:
                return
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._bytes -= existing.nbytes
            self._entries[key] = page
            self._bytes += page.nbytes
            self._evict_locked()

    def _evict_locked(self) -> None:
        while self._bytes > self._max_bytes and self._entries:
            _, evicted = self._entries.popitem(last=False)
            self._bytes -= evicted.nbytes
            self._evictions += 1

    def stats(self) -> RenderCacheStats:
        with self._lock:
            return RenderCacheStats(
                hits=self._hits,
                misses=self._misses,
                evictions=self._evictions,
                entries=len(self._entries),
                bytes_used=self._bytes,
                max_bytes=self._max_bytes,
            )


_CACHE = _RenderCache(default_render_cache_bytes())

_fingerprint_lock = threading.Lock()
_fingerprints: OrderedDict[tuple[int, int, int, int], str] = OrderedDict()


def configure_render_cache(*, max_bytes: int) -> None:
    """Resize the budget (``0`` disables and empties the cache).

    Used by the parallel extractor, where N worker processes each hold their
    own cache and the whole-machine budget has to be shared out between them.
    """
    _CACHE.configure(max_bytes)


def render_cache_max_bytes() -> int:
    return _CACHE.max_bytes


def clear_render_cache() -> None:
    """Empty the cache and reset its counters (tests, and the TUI on reload)."""
    _CACHE.clear()
    with _fingerprint_lock:
        _fingerprints.clear()


def render_cache_stats() -> RenderCacheStats:
    return _CACHE.stats()


def render_cache_get(key: tuple[str, int, int]) -> RenderedPage | None:
    return _CACHE.get(key)


def render_cache_put(key: tuple[str, int, int], page: RenderedPage) -> None:
    _CACHE.put(key, page)


def file_fingerprint(path: str | os.PathLike[str]) -> str | None:
    """SHA-256 of a file, memoized on ``(dev, ino, size, mtime_ns)``.

    Returns ``None`` when the file cannot be stat'd or read — an unreadable
    file has no identity, and the caller must then render uncached rather than
    guess at one.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return None
    memo_key = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    with _fingerprint_lock:
        cached = _fingerprints.get(memo_key)
        if cached is not None:
            _fingerprints.move_to_end(memo_key)
            return cached
    digest = _hash_file(path)
    if digest is None:
        return None
    with _fingerprint_lock:
        _fingerprints[memo_key] = digest
        _fingerprints.move_to_end(memo_key)
        while len(_fingerprints) > _FINGERPRINT_MEMO_SIZE:
            _fingerprints.popitem(last=False)
    return digest


def _hash_file(path: str | os.PathLike[str]) -> str | None:
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(_HASH_CHUNK):
                hasher.update(chunk)
    except OSError:
        return None
    return hasher.hexdigest()


def document_fingerprint(doc: Any) -> str | None:
    """Content identity of an open ``fitz.Document``, or ``None`` if it has none.

    A document opened from a stream, from a synthesized in-memory PDF, or one
    with unsaved changes has no file on disk that its pages can be keyed to, so
    it renders uncached.
    """
    name = getattr(doc, "name", None)
    if not name:
        return None
    # An open document with pending edits no longer matches the bytes on disk.
    if getattr(doc, "is_dirty", False):
        return None
    return file_fingerprint(Path(name))
