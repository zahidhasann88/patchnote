"""Validate LLM output against the input entries. Reject anything extra."""

from __future__ import annotations

from typing import Any


class ValidationFailure(Exception):  # noqa: N818
    """LLM output did not match the schema or the input set."""


def _as_dict(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValidationFailure("response is not a JSON object")
    return raw


def validate_polish(original: list[dict[str, Any]], raw: Any) -> list[dict[str, str]]:
    data = _as_dict(raw)
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
        extra_keys = set(item.keys()) - {"id", "summary"}
        # Allow a few harmless keys but reject anything that looks like a new entry field.
        forbidden = extra_keys & {"pr_number", "hashes", "section", "entries", "overview"}
        if forbidden:
            raise ValidationFailure(f"entry {index} has forbidden keys: {sorted(forbidden)}")
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
        rewritten.append({"id": ident, "summary": summary.strip()})
    if seen != original_ids:
        raise ValidationFailure("ids do not match the input set")
    return rewritten


def validate_summary(raw: Any) -> str:
    data = _as_dict(raw)
    if "entries" in data:
        raise ValidationFailure("summary response must not include entries")
    overview = data.get("overview")
    if not isinstance(overview, str) or not overview.strip():
        raise ValidationFailure("missing overview string")
    text = overview.strip()
    sentences = [
        part for part in text.replace("!", ".").replace("?", ".").split(".") if part.strip()
    ]
    if len(sentences) > 4:
        raise ValidationFailure("overview is longer than 3-4 sentences")
    if len(text) > 800:
        raise ValidationFailure("overview exceeds 800 characters")
    return text
