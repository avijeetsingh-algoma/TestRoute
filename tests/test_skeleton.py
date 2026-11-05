"""Offline tests for the milestone 1 skeleton (spec Sections 1, 9.6, 12, 15).

Covers the support modules ``settings``, ``check_env`` and ``cli``: reading
``.env`` without leaking the key, ``extends`` resolution in configs, the exact
pin rule for requirements, and the CLI behavior for commands that are not
built yet. No network access and no API key are needed.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from testroute import paths
from testroute.check_env import CONFIG_FILES, parse_requirements
from testroute.cli import main
from testroute.settings import ConfigError, load_config, load_env_settings

FAKE_KEY = "sk-test-not-a-real-key-123456"


def test_env_settings_repr_never_shows_key(tmp_path: Path) -> None:
    """The API key is read from .env but never appears in repr or str (spec rule 1.3)."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"DEEPSEEK_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    settings = load_env_settings(env_file)
    assert settings.api_key_set
    assert FAKE_KEY not in repr(settings)
    assert FAKE_KEY not in str(settings)


def test_env_settings_does_not_touch_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading .env leaves os.environ unchanged, so child processes cannot inherit the key."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"DEEPSEEK_API_KEY={FAKE_KEY}\n", encoding="utf-8")
    load_env_settings(env_file)
    assert "DEEPSEEK_API_KEY" not in os.environ


def test_env_settings_defaults_when_entries_blank(tmp_path: Path) -> None:
    """Blank or missing entries fall back to the .env.example defaults and an empty key."""
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=\nDEEPSEEK_BASE_URL=\n", encoding="utf-8")
    settings = load_env_settings(env_file)
    assert settings.env_file_exists
    assert not settings.api_key_set
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.ollama_host == "http://localhost:11434"


def test_env_settings_missing_file(tmp_path: Path) -> None:
    """A missing .env is reported through env_file_exists instead of raising."""
    settings = load_env_settings(tmp_path / "absent.env")
    assert not settings.env_file_exists
    assert not settings.api_key_set


def test_extends_merges_nested_blocks(tmp_path: Path) -> None:
    """An extending config replaces listed keys and keeps sibling nested keys."""
    (tmp_path / "base.yaml").write_text(
        "name: base\ntarget:\n  a: 1\n  b: 2\nitems: [1, 2]\n", encoding="utf-8"
    )
    (tmp_path / "child.yaml").write_text(
        "extends: base.yaml\nname: child\ntarget:\n  b: 3\nitems: [9]\n", encoding="utf-8"
    )
    merged = load_config("child.yaml", config_dir=tmp_path)
    assert merged == {"name": "child", "target": {"a": 1, "b": 3}, "items": [9]}


def test_extends_loop_is_rejected(tmp_path: Path) -> None:
    """A config chain that extends itself raises ConfigError instead of recursing forever."""
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config("a.yaml", config_dir=tmp_path)


@pytest.mark.parametrize("name", CONFIG_FILES)
def test_shipped_configs_parse(name: str) -> None:
    """Every shipped config file parses and resolves its extends chain."""
    assert isinstance(load_config(name), dict)


def test_pilot_config_inherits_main_settings() -> None:
    """The pilot config overrides trials and folder but keeps the main quality target."""
    pilot = load_config("experiment_pilot.yaml")
    assert pilot["trials"] == 1
    assert pilot["results_dir"] == "results/pilot"
    assert pilot["quality_target"]["min_feedback_strict_ms"] == 80
    assert pilot["mutation"]["mutant_cap"] == 60


def test_requirements_are_exact_pins() -> None:
    """requirements.txt pins every package with == (spec rule 1.8)."""
    pins = parse_requirements(paths.REQUIREMENTS_FILE)
    assert pins
    assert all(version for version in pins.values())


def test_unpinned_requirement_rejected(tmp_path: Path) -> None:
    """A requirement without an exact == pin is rejected by the parser."""
    req = tmp_path / "requirements.txt"
    req.write_text("pytest>=8\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_requirements(req)


def test_pending_command_exits_with_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    """A command from a later milestone is registered but reports it is not built yet."""
    assert main(["analyze"]) == 2
    assert "milestone 7" in capsys.readouterr().out


def _deepseek_transport(status: int, models: list[str], seen: list[str]):
    """Mock DeepSeek API answering /models with ``status`` and a model list."""
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/user/balance"):
            return httpx.Response(200, json={"is_available": True})
        if status != 200:
            return httpx.Response(status, json={"error": {"message": f"Your api key: ****{FAKE_KEY[-4:]} is invalid"}})
        return httpx.Response(200, json={"data": [{"id": m} for m in models]})

    return httpx.MockTransport(handler)


@pytest.fixture
def env_with_key(monkeypatch: pytest.MonkeyPatch):
    """Pretend .env holds FAKE_KEY, without touching the real .env file."""
    from testroute import check_env
    from testroute.settings import EnvSettings

    monkeypatch.setattr(
        check_env, "load_env_settings", lambda: EnvSettings(True, FAKE_KEY, "https://deepseek.test", "http://127.0.0.1:9")
    )


def test_check_env_rejected_key_fails_even_at_setup(env_with_key) -> None:
    """A key DeepSeek rejects fails check-env in setup mode too, and no key text is printed."""
    from testroute.check_env import FAIL, check_deepseek_key

    seen: list[str] = []
    results = check_deepseek_key(setup_mode=True, transport=_deepseek_transport(401, [], seen))
    assert [r.status for r in results] == [FAIL]
    assert "HTTP 401" in results[0].detail and FAKE_KEY[-4:] not in results[0].detail
    assert seen == ["/models"]


def test_check_env_valid_key_uses_only_free_endpoints(env_with_key) -> None:
    """A valid key is verified with GET /models and /user/balance only; no completion is requested."""
    from testroute.check_env import OK, check_deepseek_key

    seen: list[str] = []
    results = check_deepseek_key(setup_mode=False, transport=_deepseek_transport(200, ["deepseek-flash"], seen))
    assert [r.status for r in results[:2]] == [OK, OK]
    assert seen == ["/models", "/user/balance"]


def test_check_env_missing_model_is_soft_at_setup(env_with_key) -> None:
    """A configured model missing from the list warns during setup and fails in run_all mode."""
    from testroute.check_env import FAIL, WARN, check_deepseek_key

    setup = check_deepseek_key(setup_mode=True, transport=_deepseek_transport(200, ["other"], []))
    normal = check_deepseek_key(setup_mode=False, transport=_deepseek_transport(200, ["other"], []))
    assert setup[1].status == WARN and normal[1].status == FAIL
