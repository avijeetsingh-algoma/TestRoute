"""Loading of .env settings and YAML configuration files.

Component: support module shared by C1, C4, C5 and ``check_env`` (not a row
of the spec Section 5 table).

Responsibility:
    1. Read ``.env`` with python-dotenv into an :class:`EnvSettings` object.
       The values are read straight from the file and are NOT copied into
       ``os.environ``, so the API key never reaches the environment that
       child processes inherit (spec rule 1.3).
    2. Load a YAML config from ``config/`` and resolve its ``extends`` key
       (see config/README.md) into one merged dictionary.

Receives: the project ``.env`` file and YAML files in ``config/``.
Produces: :class:`EnvSettings` and plain ``dict`` configs.
Talks to: ``paths`` (file locations). Used by ``check_env`` now and by the
experiment runner and providers in later milestones.
Spec sections: 1.3 (only secret, read from .env), 7 (endpoint settings),
9.6 (configs), 12 (.env.example).
Feedback IDs: FB12 (model and budget settings come from fixed config files).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from testroute import paths

# Defaults used when .env lacks a non-secret entry. They equal the values in
# .env.example (spec Section 12), so a missing line behaves like the template.
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"


class ConfigError(Exception):
    """Raised when a YAML config is missing, unreadable or malformed."""


@dataclass(frozen=True)
class EnvSettings:
    """Values read from ``.env``.

    Attributes:
        env_file_exists: True when the ``.env`` file was found.
        deepseek_api_key: The DeepSeek key, or an empty string. Declared with
            ``repr=False`` so printing or logging this object (including in a
            traceback) never shows the key (spec rule 1.3).
        deepseek_base_url: OpenAI-compatible DeepSeek endpoint.
        ollama_host: Base URL of the local Ollama server.
    """

    env_file_exists: bool
    deepseek_api_key: str = field(repr=False)
    deepseek_base_url: str
    ollama_host: str

    @property
    def api_key_set(self) -> bool:
        """True when a non-blank DeepSeek key is present. Never exposes it."""
        return bool(self.deepseek_api_key.strip())


def load_env_settings(env_file: Path | None = None) -> EnvSettings:
    """Read ``.env`` into an :class:`EnvSettings`.

    The file is parsed with ``dotenv_values``, which returns a dict and leaves
    ``os.environ`` untouched. This is a deliberate choice: the sandbox (C8)
    builds child environments from an allowlist, and keeping the key out of
    ``os.environ`` adds a second layer of protection.

    Args:
        env_file: Path of the file to read. Defaults to ``<root>/.env``.

    Returns:
        The parsed settings. Missing entries fall back to the defaults from
        ``.env.example``; a missing key becomes an empty string.

    Raises:
        Nothing for a missing file (``env_file_exists`` is then False).

    Side effects:
        Reads one file. Writes nothing and does not modify ``os.environ``.
    """
    path = env_file if env_file is not None else paths.ENV_FILE
    exists = path.is_file()
    values: dict[str, str | None] = dotenv_values(path) if exists else {}

    def _get(name: str, default: str) -> str:
        # dotenv returns None for a line like "NAME" without "=", and "" for
        # "NAME=". Both are treated as "not set" and replaced by the default.
        value = values.get(name)
        return value.strip() if value and value.strip() else default

    return EnvSettings(
        env_file_exists=exists,
        deepseek_api_key=(values.get("DEEPSEEK_API_KEY") or "").strip(),
        deepseek_base_url=_get("DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL),
        ollama_host=_get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge ``override`` into a copy of ``base``.

    Mappings are merged key by key so an override of one nested setting keeps
    its siblings. Every other value type, lists included, is replaced whole.

    Args:
        base: The dictionary loaded from the extended file.
        override: The dictionary of the extending file.

    Returns:
        A new merged dictionary; neither input is modified.
    """
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(name: str, config_dir: Path | None = None) -> dict[str, Any]:
    """Load a YAML config from ``config/`` and resolve ``extends`` chains.

    Args:
        name: File name inside the config folder, for example
            ``"experiment_pilot.yaml"``.
        config_dir: Folder to read from. Defaults to ``<root>/config``.

    Returns:
        The merged configuration. The ``extends`` key itself is removed.

    Raises:
        ConfigError: if a file is missing, is not valid YAML, does not hold a
            mapping at the top level, or the ``extends`` chain loops.

    Side effects:
        Reads one or more files in the config folder.
    """
    folder = config_dir if config_dir is not None else paths.CONFIG_DIR
    seen: list[str] = []

    def _load(file_name: str) -> dict[str, Any]:
        # Guard against a file that extends itself directly or indirectly,
        # which would otherwise recurse forever.
        if file_name in seen:
            chain = " -> ".join(seen + [file_name])
            raise ConfigError(f"extends loop in config files: {chain}")
        seen.append(file_name)
        path = folder / file_name
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {file_name}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{file_name} must contain a mapping at the top level")
        parent = data.pop("extends", None)
        if parent is None:
            return data
        return _deep_merge(_load(str(parent)), data)

    return _load(name)
