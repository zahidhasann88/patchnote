from __future__ import annotations

from pathlib import Path

import pytest

from patchnote.config import (
    ConfigError,
    find_config_path,
    load_config,
    parse_config,
    write_init_config,
)
from patchnote.model import Style


def test_defaults() -> None:
    cfg = parse_config({})
    assert cfg.style is Style.KEEPACHANGELOG
    assert "chore" in cfg.skip_types
    assert "Added" in cfg.sections
    assert cfg.type_to_section()["feat"] == "Added"
    assert cfg.label_to_section()["bug"] == "Fixed"


def test_invalid_temperature() -> None:
    with pytest.raises(ConfigError) as exc:
        parse_config({"ai": {"temperature": 9}})
    assert "temperature" in str(exc.value)


def test_unknown_key_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_config({"not_a_real_key": True})


def test_style_conventional_sections() -> None:
    cfg = parse_config({"style": "conventional"})
    assert "Features" in cfg.sections
    assert cfg.type_to_section()["fix"] == "Bug Fixes"


def test_init_and_load(tmp_path: Path) -> None:
    path = write_init_config(tmp_path)
    assert path.is_file()
    cfg = load_config(tmp_path, path)
    assert cfg.style is Style.KEEPACHANGELOG
    with pytest.raises(ConfigError):
        write_init_config(tmp_path)


def test_init_force(tmp_path: Path) -> None:
    write_init_config(tmp_path)
    write_init_config(tmp_path, force=True)


def test_find_config_walks_up(tmp_path: Path) -> None:
    (tmp_path / "patchnote.yaml").write_text("style: conventional\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    found = find_config_path(nested)
    assert found == tmp_path / "patchnote.yaml"


def test_missing_explicit_config(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        find_config_path(tmp_path, tmp_path / "nope.yaml")


def test_bad_yaml(tmp_path: Path) -> None:
    path = tmp_path / "patchnote.yaml"
    path.write_text(":\n: :\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path, path)
