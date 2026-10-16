import httpx
import pytest
from pydantic import ValidationError

from app.contracts import FINANCIAL_FIELDS, FinancialExtraction
from app.errors import PermanentError, RetryableError


def valid_payload(**overrides):
    empty = {"value": None, "source": None, "confidence": 0}
    payload = {"company_name": "Alpha", "fiscal_year": 2024, "currency": "USD", "unit_multiplier": 1,
               **{f: empty for f in FINANCIAL_FIELDS}}
    payload["revenue"] = {"value": 12_500_000, "confidence": 0.97,
                          "source": {"page": 2, "quote": "Revenue 12,500,000"}}
    payload.update(overrides)
    return payload


def test_valid_payload_parses():
    x = FinancialExtraction.model_validate(valid_payload())
    assert x.revenue.value == 12_500_000 and x.revenue.source.page == 2


@pytest.mark.parametrize("overrides", [
    {"company_name": ""},
    {"currency": "dollars"},
    {"unit_multiplier": 7},
    {"revenue": {"value": "lots", "source": None, "confidence": 0.9}},
    {"revenue": {"value": 1, "source": None, "confidence": 1.5}},
    {"hallucinated_field": 1},  # extra keys rejected
])
def test_malformed_llm_output_is_rejected(overrides):
    with pytest.raises(ValidationError):
        FinancialExtraction.model_validate(valid_payload(**overrides))


def test_missing_field_is_rejected():
    payload = valid_payload()
    del payload["net_income"]
    with pytest.raises(ValidationError):
        FinancialExtraction.model_validate(payload)


class FakeMessages:
    def __init__(self, exc=None, response=None):
        self.exc, self.response = exc, response

    def parse(self, **kwargs):
        if self.exc:
            raise self.exc
        return self.response


def _claude(monkeypatch, **kw):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from app.extraction import ClaudeExtractor
    ex = ClaudeExtractor()
    ex.client = type("C", (), {"messages": FakeMessages(**kw)})()
    return ex


def _http_error(cls, status):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message=f"HTTP {status}", response=httpx.Response(status, request=req), body=None)


def test_claude_error_mapping(monkeypatch):
    import anthropic
    from app.contracts import DocumentContent, PageText
    content = DocumentContent(kind="text", pages=[PageText(page=1, text="x")])

    for exc, expected in [
        (_http_error(anthropic.RateLimitError, 429), RetryableError),
        (_http_error(anthropic.InternalServerError, 529), RetryableError),
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), RetryableError),
        (_http_error(anthropic.BadRequestError, 400), PermanentError),
        (_http_error(anthropic.AuthenticationError, 401), PermanentError),
    ]:
        with pytest.raises(expected):
            _claude(monkeypatch, exc=exc).extract(content, "f.txt")


def test_claude_refusal_is_permanent(monkeypatch):
    from app.contracts import DocumentContent, PageText
    content = DocumentContent(kind="text", pages=[PageText(page=1, text="x")])
    resp = type("R", (), {"stop_reason": "refusal", "parsed_output": None})()
    with pytest.raises(PermanentError):
        _claude(monkeypatch, response=resp).extract(content, "f.txt")
