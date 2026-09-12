from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
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

MIN_CACHE_BYTES = 128 * _MIB
MAX_CACHE_BYTES = 1024 * _MIB
FALLBACK_CACHE_BYTES = 256 * _MIB

CACHE_BYTES_ENV_VAR = "OPENGRADER_RENDER_CACHE_MB"

_FINGERPRINT_MEMO_SIZE = 512

_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class RenderedPage:
    mode: str
    width: int
    height: int
    data: bytes

    @property
    def nbytes(self) -> int:
        return len(self.data)

    @classmethod
    def from_pixmap(cls, pix: Any) -> RenderedPage:
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
        from PIL import Image as PILImage

        return PILImage.frombytes(self.mode, (self.width, self.height), self.data)


_PIXMAP_MODES = {1: "L", 3: "RGB", 4: "CMYK"}


@dataclass(frozen=True)
class RenderCacheStats:
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
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        return None
    if pages <= 0 or page_size <= 0:
        return None
    return pages * page_size


def default_render_cache_bytes() -> int:
    raw = os.environ.get(CACHE_BYTES_ENV_VAR)
    if raw is not None and raw.strip():
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
    _CACHE.configure(max_bytes)


def render_cache_max_bytes() -> int:
    return _CACHE.max_bytes


def clear_render_cache() -> None:
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
    name = getattr(doc, "name", None)
    if not name:
        return None
    if getattr(doc, "is_dirty", False):
        return None
    return file_fingerprint(Path(name))
