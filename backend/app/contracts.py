"""Data contracts shared by every pipeline stage.

The LLM output, the database rows and the API responses are all validated
against these models. The frontend mirrors them with Zod (frontend/lib/contracts.ts),
so a breaking change here must be reflected there.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .money import MAX_MINOR

PIPELINE_VERSION = "extract-v1"

# Money in API and storage contracts is integer minor units (see app/money.py).
# The LLM contract below is the one exception: it reports numbers as printed.


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# LLM extraction contract (what the model must return)
# ---------------------------------------------------------------------------

FINANCIAL_FIELDS: tuple[str, ...] = (
    "revenue",
    "cost_of_goods_sold",
    "gross_profit",
    "operating_expenses",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "total_debt",
    "cash",
)


class SourceRef(Strict):
    page: int | None = Field(None, ge=1, description="1-indexed page (PDF) or None")
    sheet: str | None = Field(None, description="Worksheet name for spreadsheets")
    cell: str | None = Field(None, description="Cell reference such as B7")
    quote: str = Field(..., max_length=300, description="Verbatim text the value was read from")


class ExtractedValue(Strict):
    """A single figure. value=None means the document gave no reliable evidence."""

    value: float | None
    source: SourceRef | None
    confidence: float = Field(..., ge=0, le=1)


class FinancialExtraction(Strict):
    company_name: str = Field(..., min_length=1)
    reporting_period_end: str | None = Field(
        None, description="ISO date (YYYY-MM-DD) of the period end, if stated"
    )
    fiscal_year: int | None = Field(None, ge=1900, le=2100)
    currency: str | None = Field(None, pattern=r"^[A-Z]{3}$")
    unit_multiplier: Literal[1, 1000, 1000000] = Field(
        1, description="1 if figures are in units, 1000 if in thousands, 1000000 if in millions"
    )
    revenue: ExtractedValue
    cost_of_goods_sold: ExtractedValue
    gross_profit: ExtractedValue
    operating_expenses: ExtractedValue
    operating_income: ExtractedValue
    net_income: ExtractedValue
    total_assets: ExtractedValue
    total_liabilities: ExtractedValue
    total_equity: ExtractedValue
    total_debt: ExtractedValue
    cash: ExtractedValue
    notes: list[str] = Field(default_factory=list, max_length=10)


# ---------------------------------------------------------------------------
# Document processing
# ---------------------------------------------------------------------------


class PageText(Strict):
    page: int | None = None
    sheet: str | None = None
    text: str


class DocumentContent(Strict):
    kind: Literal["pdf", "xlsx", "text"]
    pages: list[PageText]

    def as_prompt(self) -> str:
        parts = []
        for p in self.pages:
            label = f"[PAGE {p.page}]" if p.page else f"[SHEET {p.sheet}]"
            parts.append(f"{label}\n{p.text}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Domain / API models
# ---------------------------------------------------------------------------


class DocumentStatus(StrEnum):
    uploaded = "uploaded"
    processing = "processing"
    needs_review = "needs_review"
    validated = "validated"
    failed = "failed"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    dead = "dead"  # moved to the dead-letter queue after exhausting retries


class FieldStatus(StrEnum):
    extracted = "extracted"  # passed all checks, awaiting analyst sign-off
    flagged = "flagged"  # implicated in a validation issue
    missing = "missing"  # model found no reliable evidence
    approved = "approved"
    corrected = "corrected"


class Evidence(StrEnum):
    """Deterministic check of the model's cited source (see app/evidence.py)."""

    verified = "verified"  # quote found in the cited page/sheet and contains the value
    no_source = "no_source"  # a value with no quote at all
    quote_not_found = "quote_not_found"
    value_not_in_quote = "value_not_in_quote"


class Severity(StrEnum):
    error = "error"
    warning = "warning"
    info = "info"


class ProjectCreate(Strict):
    name: str = Field(..., min_length=1, max_length=200)
    company_name: str = Field(..., min_length=1, max_length=200)


class Project(BaseModel):
    id: str
    name: str
    company_name: str
    created_at: datetime
    document_count: int = 0
    open_issue_count: int = 0


class Document(BaseModel):
    id: str
    project_id: str
    file_name: str
    content_type: str
    size_bytes: int
    sha256: str
    status: DocumentStatus
    fiscal_year: int | None = None
    error: str | None = None
    created_at: datetime
    excluded: bool = False
    excluded_reason: str | None = None


class UploadResult(BaseModel):
    document: Document
    duplicate: bool
    job_id: str | None


class Job(BaseModel):
    id: str
    document_id: str
    file_name: str | None = None
    kind: str
    status: JobStatus
    attempts: int
    max_attempts: int
    next_run_at: datetime
    last_error: str | None
    error_kind: Literal["retryable", "permanent"] | None
    updated_at: datetime


class ExtractedField(BaseModel):
    id: str
    document_id: str
    name: str
    value_minor: int | None = Field(..., description="Integer hundredths of the currency unit")
    original_value_minor: int | None
    page: int | None
    sheet: str | None
    cell: str | None
    quote: str | None
    confidence: float
    status: FieldStatus
    evidence: Evidence | None = Field(None, description="None when there is no value to check")
    version: int = Field(..., ge=1, description="Optimistic concurrency token; send back on review")


class ValidationIssue(BaseModel):
    id: str
    document_id: str | None
    project_id: str
    rule: str
    severity: Severity
    message: str
    fields: list[str]
    expected_minor: int | None = None
    actual_minor: int | None = None
    resolved: bool = False


class ReviewAction(Strict):
    action: Literal["approve", "correct"]
    value_minor: int | None = Field(None, ge=-MAX_MINOR, le=MAX_MINOR, description="Required for 'correct'")
    # Required for 'correct' (checked below) and for approving a flagged value (checked
    # in the API, which knows the field's status).
    note: Annotated[str, StringConstraints(strip_whitespace=True, max_length=1000)] | None = None
    expected_version: int = Field(..., ge=1, description="The field version the reviewer saw")
    # TODO(auth): replace with the authenticated user's identity.
    reviewer: str = Field("analyst", min_length=1, max_length=100)

    @model_validator(mode="after")
    def _correction_is_complete(self) -> "ReviewAction":
        if self.action == "correct":
            if self.value_minor is None:
                raise ValueError("A correction needs a value")
            if not self.note:
                raise ValueError("A correction needs a note explaining the source of the new value")
        return self


class DocumentExclusion(Strict):
    # Stripped before the length check, so a whitespace-only reason is rejected.
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
    reviewer: str = Field("analyst", min_length=1, max_length=100)


class Review(BaseModel):
    id: str
    field_id: str
    action: str
    old_value_minor: int | None
    new_value_minor: int | None
    note: str | None
    reviewer: str
    created_at: datetime


class DocumentDetail(BaseModel):
    document: Document
    company_name: str | None
    currency: str | None
    reporting_period_end: str | None
    fields: list[ExtractedField]
    issues: list[ValidationIssue]
    reviews: list[Review]
    jobs: list[Job]


class ProjectDetail(BaseModel):
    project: Project
    documents: list[Document]
    issues: list[ValidationIssue]
    jobs: list[Job]
    report_ready: bool
