"""Validate LLM output against the input entries. Reject anything extra."""

from __future__ import annotations

import re
from typing import Any


class ValidationFailure(Exception):  # noqa: N818
    """LLM output did not match the schema or the input set."""


def _as_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationFailure("response is not a JSON object")
    return raw


def validate_polish(original: list[dict[str, Any]], raw: Any) -> list[dict[str, str]]:
    data = _as_dict(raw)
    if set(data) != {"entries"}:
        raise ValidationFailure("polish response must contain only entries")
    entries = data.get("entries")
    if not isinstance(entries, list):
        raise ValidationFailure("missing entries array")
    if len(entries) != len(original):
        raise ValidationFailure(
            f"entry count changed (got {len(entries)}, expected {len(original)})"
        )
    original_ids = [str(item["id"]) for item in original]
    seen: list[str] = []
    rewritten: list[dict[str, str]] = []
    for index, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValidationFailure(f"entry {index} is not an object")
        if set(item) != {"id", "summary"}:
            raise ValidationFailure(f"entry {index} must contain only id and summary")
        ident = item.get("id")
        summary = item.get("summary")
        if not isinstance(ident, str) or not ident:
            raise ValidationFailure(f"entry {index} missing id")
        if not isinstance(summary, str) or not summary.strip():
            raise ValidationFailure(f"entry {index} missing summary")
        if ident != original_ids[index]:
            raise ValidationFailure(
                f"id mismatch at {index}: got {ident!r}, expected {original_ids[index]!r}"
            )
        if ident in seen:
            raise ValidationFailure(f"duplicate id {ident!r}")
        seen.append(ident)
        # Guard against injected "new" work disguised as a rewritten summary that
        # clearly tries to add extra bullets or PR numbers not in the source.
        if "\n-" in summary or summary.strip().startswith("- "):
            raise ValidationFailure(f"entry {ident} summary looks like extra markdown bullets")
        if any(ord(char) < 32 for char in summary) or len(summary) > 1000:
            raise ValidationFailure("summary must be a short single line")
        original_numbers = set(re.findall(r"#(\d+)", str(original[index].get("summary", ""))))
        if original[index].get("pr_number") is not None:
            original_numbers.add(str(original[index]["pr_number"]))
        if not set(re.findall(r"#(\d+)", summary)) <= original_numbers:
            raise ValidationFailure("summary introduced a new PR or issue reference")
        if summary.lstrip().startswith(("#", "* ", "+ ", ">", "<")):
            raise ValidationFailure("summary must not add markdown blocks or HTML")
        rewritten.append({"id": ident, "summary": summary.strip()})
    if seen != original_ids:
        raise ValidationFailure("ids do not match the input set")
    return rewritten


def validate_summary(raw: Any) -> str:
    data = _as_dict(raw)
    if set(data) != {"overview"}:
        raise ValidationFailure("summary response must contain only overview")
    overview = data.get("overview")
    if not isinstance(overview, str) or not overview.strip():
        raise ValidationFailure("missing overview string")
    text = overview.strip()
    if any(ord(char) < 32 for char in text) or text.startswith(("#", "- ", "* ", "+ ", "<")):
        raise ValidationFailure("overview must be plain single-line text")
    sentences = [
        part for part in text.replace("!", ".").replace("?", ".").split(".") if part.strip()
    ]
    if len(sentences) > 4:
        raise ValidationFailure("overview is longer than 3-4 sentences")
    if len(text) > 800:
        raise ValidationFailure("overview exceeds 800 characters")
    return text
