"""Persisted scan batches and the web intake contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from opengrader.core.submissions.region_crop import RosterStudent

MAX_SCAN_BYTES = 250 * 1024 * 1024
MAX_SCAN_PAGES = 3000
ScanMode = Literal["automatic", "fixed", "individual"]
SplitMode = Literal["fixed", "individual"]
ScanStatus = Literal["uploaded", "checking", "review", "queued", "processing", "ready", "failed", "removed"]


class ScanModel(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class PageSelection(ScanModel):
    number: int = Field(ge=1)
    rotation: Literal[0, 90, 180, 270] = 0


class ScanGroup(ScanModel):
    key: str
    pages: list[PageSelection]
    submission_id: int | None = None
    scan_warnings: list[str] = Field(default_factory=list)
    error: str = ""
    assignment_version: str | None = None
    student: RosterStudent | None = None


class ScanFinding(ScanModel):
    page: int | None = None
    severity: str
    detail: str


class ScanBatch(ScanModel):
    id: str
    filename: str
    sha256: str
    page_count: int
    created_at: str
    status: ScanStatus = "uploaded"
    revision: int = 1
    mode: ScanMode = "fixed"
    pages_per_submission: int | None = None
    groups: list[ScanGroup] = Field(default_factory=list)
    excluded_pages: list[int] = Field(default_factory=list)
    findings: list[ScanFinding] = Field(default_factory=list)
    message: str = "Choose how to separate this scan."
    started: bool = False
    assignment_version: str | None = None
    version_scores: dict[int, dict[str, int]] = Field(default_factory=dict)


class ScanList(ScanModel):
    batches: list[ScanBatch]
    max_file_bytes: int = MAX_SCAN_BYTES
    max_pages: int = MAX_SCAN_PAGES


class AnalyzeScan(BaseModel):
    expected_revision: int
    mode: SplitMode = "fixed"
    pages_per_submission: int | None = Field(default=None, ge=1, le=MAX_SCAN_PAGES)


class ReviewScan(BaseModel):
    expected_revision: int
    groups: list[list[PageSelection]] = Field(min_length=1, max_length=MAX_SCAN_PAGES)
    excluded_pages: list[int] = Field(default_factory=list, max_length=MAX_SCAN_PAGES)


class ProcessScan(BaseModel):
    expected_revision: int
    acknowledge_findings: bool = False
    assignment_version: str | None = None
    group_versions: dict[str, str | None] = Field(default_factory=dict)


class ScanRevision(BaseModel):
    expected_revision: int
