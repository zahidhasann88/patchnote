"""JSON changelog rendering."""

from __future__ import annotations

import json

from patchnote.model import Changelog


def render_json(changelog: Changelog) -> str:
    payload = changelog.model_dump(mode="json")
    return json.dumps(payload, indent=2, sort_keys=False) + "\n"
