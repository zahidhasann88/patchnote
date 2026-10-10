"""Optional LLM polishing. Rule-based classification is the source of truth."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from patchnote.ai.cache import Cache
from patchnote.ai.client import LLMClient, LLMError
from patchnote.ai.prompts import polish_messages, summary_messages
from patchnote.ai.validate import ValidationFailure, validate_polish, validate_summary
from patchnote.config import AiConfig
from patchnote.model import AiMode, Changelog

WarnFn = Callable[[str], None]


def apply_ai(
    changelog: Changelog,
    config: AiConfig,
    mode: AiMode,
    client: LLMClient,
    cache: Cache,
    *,
    include_bodies: bool = False,
    warn: WarnFn | None = None,
) -> Changelog:
    """Rewrite entry text and/or add an overview. Falls back on any failure."""
    notifier = warn or (lambda _m: None)
    if mode in {AiMode.POLISH, AiMode.BOTH}:
        changelog = _polish(changelog, config, client, cache, include_bodies, notifier)
    if mode in {AiMode.SUMMARY, AiMode.BOTH}:
        changelog = _summarize(changelog, config, client, cache, include_bodies, notifier)
    return changelog


def _polish(
    changelog: Changelog,
    config: AiConfig,
    client: LLMClient,
    cache: Cache,
    include_bodies: bool,
    warn: WarnFn,
) -> Changelog:
    payload = changelog.to_ai_payload(include_bodies=include_bodies)
    if not payload["entries"]:
        return changelog
    cache_key = cache.key("polish", config.model, config.base_url, payload)
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            rewritten = validate_polish(payload["entries"], cached)
        except ValidationFailure as exc:
            warn(f"Ignoring stale AI cache: {exc}")
        else:
            return _apply_rewrites(changelog, rewritten)
    messages = polish_messages(payload["entries"], include_bodies=include_bodies)
    try:
        raw = client.complete_json(messages, model=config.model)
        rewritten = validate_polish(payload["entries"], raw)
    except (LLMError, ValidationFailure) as exc:
        warn(f"AI polish failed ({exc}); using rule-based text")
        return changelog
    try:
        cache.put(cache_key, {"entries": rewritten})
    except OSError:
        warn("Could not write AI cache; keeping the validated response")
    return _apply_rewrites(changelog, rewritten)


def _summarize(
    changelog: Changelog,
    config: AiConfig,
    client: LLMClient,
    cache: Cache,
    include_bodies: bool,
    warn: WarnFn,
) -> Changelog:
    payload = changelog.to_ai_payload(include_bodies=include_bodies)
    if not payload["entries"]:
        return changelog
    cache_key = cache.key("summary", config.model, config.base_url, payload)
    cached = cache.get(cache_key)
    if cached is not None:
        try:
            overview = validate_summary(cached)
        except ValidationFailure as exc:
            warn(f"Ignoring stale AI cache: {exc}")
        else:
            return changelog.model_copy(update={"overview": overview})
    messages = summary_messages(payload["entries"], version=changelog.version)
    try:
        raw = client.complete_json(messages, model=config.model)
        overview = validate_summary(raw)
    except (LLMError, ValidationFailure) as exc:
        warn(f"AI summary failed ({exc}); omitting overview")
        return changelog
    try:
        cache.put(cache_key, {"overview": overview})
    except OSError:
        warn("Could not write AI cache; keeping the validated response")
    return changelog.model_copy(update={"overview": overview})


def _apply_rewrites(changelog: Changelog, rewritten: list[dict[str, str]]) -> Changelog:
    by_id = {item["id"]: item["summary"] for item in rewritten}
    new_breaking = []
    for entry in changelog.breaking:
        if entry.id in by_id:
            new_breaking.append(entry.model_copy(update={"summary": by_id[entry.id]}))
        else:
            new_breaking.append(entry)
    new_sections = {}
    for name, entries in changelog.sections.items():
        updated = []
        for entry in entries:
            if entry.id in by_id:
                updated.append(entry.model_copy(update={"summary": by_id[entry.id]}))
            else:
                updated.append(entry)
        new_sections[name] = updated
    return changelog.model_copy(update={"breaking": new_breaking, "sections": new_sections})


def default_cache(cache_dir: str | Path) -> Cache:
    return Cache(Path(cache_dir))
