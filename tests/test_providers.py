"""Offline tests of the real providers (C5) with mocked HTTP transports.

No network and no API key are used: Ollama and DeepSeek requests are served
by httpx.MockTransport handlers that also record what was sent. Covers the
retry policy, the locked request settings (thinking disabled, per-call seed),
usage parsing, secret-free error messages, replay and provider wiring.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from fakes import DATA_DIR, STRONG_TESTS, WEAK_TESTS, ScriptedAnalyzer
from testroute.controller import RunController
from testroute.llm.base import InfraError, LLMRequest, TransientError, call_with_retries
from testroute.llm.deepseek import DeepSeekProvider
from testroute.llm.factory import ProviderUnavailable, build_providers
from testroute.llm.fake import FakeProvider
from testroute.llm.ollama import OllamaProvider
from testroute.llm.replay import ReplayError, ReplayProvider
from testroute.records import ResultsStore
from testroute.router import parse_condition
from testroute.settings import EnvSettings, load_config
from testroute.subjects import load_subject

FAKE_KEY = "sk-unit-test-key-0123456789abcdef"


def _request(trial: int = 2) -> LLMRequest:
    return LLMRequest("run", 1, "local", "SYS", "USER", "sha", "fid", "L-F4", trial)


def test_retry_backoff_and_retry_after() -> None:
    """Transient errors are retried with exponential backoff; Retry-After wins for 429 (spec C5)."""
    waits: list[float] = []
    attempts = iter([TransientError("503"), TransientError("429", retry_after_s=7.0), "ok"])

    def send():
        item = next(attempts)
        if isinstance(item, Exception):
            raise item
        return item

    result, retries = call_with_retries(send, sleep=waits.append)
    assert (result, retries, waits) == ("ok", 2, [2.0, 7.0])


def test_retry_gives_up_as_infra() -> None:
    """After 3 retries a transient failure becomes an INFRA error that records the retries."""
    def send():
        raise TransientError("timeout")

    with pytest.raises(InfraError) as info:
        call_with_retries(send, sleep=lambda s: None)
    assert info.value.retries == 3


def _ollama_handler(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b", "digest": "abc123"}]})
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.0-test"})
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "qwen2.5-coder:7b", "message": {"content": "```python\nx = 1\n```"}, "done_reason": "stop",
            "prompt_eval_count": 120, "eval_count": 30, "prompt_eval_duration": 1_000_000_000,
            "eval_duration": 2_000_000_000, "load_duration": 9_000_000_000, "total_duration": 12_000_000_000,
        })
    return handler


def test_ollama_request_and_result() -> None:
    """Ollama gets the spec 2 options with seed 1000 x trial + call; compute time excludes model loading."""
    seen: list[dict] = []
    provider = OllamaProvider("http://ollama.test", "qwen2.5-coder:7b", transport=httpx.MockTransport(_ollama_handler(seen)))
    result = provider.complete(_request(trial=3))
    body = seen[0]
    assert body["stream"] is False and body["keep_alive"] == "30m"
    assert body["options"] == {"temperature": 0.2, "num_predict": 2048, "num_ctx": 8192, "seed": 3001}
    assert result.seed == 3001
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert (result.input_tokens, result.output_tokens, result.fingerprint) == (120, 30, "abc123")
    assert result.local_eval_s == pytest.approx(3.0)
    assert provider.health_check() == "0.0-test"


def test_ollama_missing_model_is_infra() -> None:
    """A model that is not pulled is an INFRA error with the pull command, never another model."""
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "other:1b", "digest": "x"}]})

    provider = OllamaProvider("http://ollama.test", "qwen2.5-coder:7b", transport=httpx.MockTransport(handler))
    with pytest.raises(InfraError, match="ollama pull qwen2.5-coder:7b"):
        provider.complete(_request())


def _deepseek(handler) -> DeepSeekProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return DeepSeekProvider(FAKE_KEY, "https://deepseek.test", "deepseek-flash", http_client=client,
                            retry={"max_retries": 1, "backoff_base_s": 0, "backoff_factor": 1})


def test_deepseek_sends_thinking_disabled_and_parses_usage() -> None:
    """Every DeepSeek request disables thinking; cache, reasoning, model and fingerprint are recorded."""
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "deepseek-flash-0101",
            "system_fingerprint": "fp_test",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                      "prompt_cache_hit_tokens": 64, "prompt_cache_miss_tokens": 36,
                      "completion_tokens_details": {"reasoning_tokens": 0}},
        })

    result = _deepseek(handler).complete(_request())
    body = seen[0]
    assert body["thinking"] == {"type": "disabled"}
    assert body["model"] == "deepseek-flash" and body["temperature"] == 0.2 and body["max_tokens"] == 2048
    assert "seed" not in body
    assert (result.cache_hit_tokens, result.cache_miss_tokens, result.reasoning_tokens) == (64, 36, 0)
    assert (result.model, result.fingerprint, result.provider) == ("deepseek-flash-0101", "fp_test", "deepseek")


def test_deepseek_errors_never_contain_the_key() -> None:
    """A rejected key is an immediate INFRA error whose message has no key material (spec rule 1.3)."""
    def handler(request):
        return httpx.Response(401, json={"error": {"message": f"Authentication Fails, key {FAKE_KEY} is invalid"}})

    with pytest.raises(InfraError) as info:
        _deepseek(handler).complete(_request())
    assert FAKE_KEY not in str(info.value) and "401" in str(info.value)
    assert FAKE_KEY not in repr(_deepseek(handler))


def test_deepseek_retries_server_errors() -> None:
    """HTTP 5xx is retried before it becomes INFRA."""
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(503, json={"error": {"message": "busy"}})

    with pytest.raises(InfraError):
        _deepseek(handler).complete(_request())
    assert len(calls) == 2


def test_replay_reproduces_a_run_and_checks_prompt_hash(tmp_path: Path) -> None:
    """Replaying a recorded run gives the same results labelled replay; a changed prompt is rejected."""
    subject = load_subject(DATA_DIR / "classify.py", "classify", function_id="tiny_classify")
    config = load_config("experiment_main.yaml")

    def run(store: ResultsStore, provider):
        return RunController(subject=subject, condition=parse_condition("L-F4"), trial=1,
                             providers={"local": provider}, analyzer=ScriptedAnalyzer(store),
                             store=store, config=config).execute()

    live = run(ResultsStore(tmp_path / "live"), FakeProvider([WEAK_TESTS, STRONG_TESTS]))
    replay = ReplayProvider(tmp_path / "live")
    again = run(ResultsStore(tmp_path / "replay"), replay)
    assert again["providers"] == ["replay"] and again["replay"] is True
    assert again["heldout_strict_ms"] == live["heldout_strict_ms"] and again["attempts"] == live["attempts"]
    log = next((tmp_path / "replay" / "artifacts").glob("*/run.log")).read_text(encoding="utf-8")
    assert "[REPLAY]" in log

    changed = LLMRequest("r", 1, "local", "SYS", "different", "other-sha", "tiny_classify", "L-F4", 1)
    with pytest.raises(ReplayError, match="prompt hash differs"):
        replay.complete(changed)


def test_factory_reports_missing_key_and_unreachable_ollama() -> None:
    """Without a key or a reachable Ollama the factory stops before any call, with instructions."""
    models = load_config("models.yaml")
    no_key = EnvSettings(True, "", "https://api.deepseek.com", "http://127.0.0.1:9")
    with pytest.raises(ProviderUnavailable, match="DEEPSEEK_API_KEY is empty"):
        build_providers(parse_condition("C-F4"), models, no_key)
    with pytest.raises(ProviderUnavailable, match="choose a cloud condition"):
        build_providers(parse_condition("L-F4"), models, no_key)


def test_ollama_seed_per_call_differs_and_reruns_match(tmp_path: Path) -> None:
    """Seeds are 1000 x trial + call number: distinct per call, identical on a rerun, recorded in calls.jsonl.

    The fake Ollama server always returns the same weak suite, so the run uses
    all 4 calls of L-F4 (spec Sections 2 and 3, seed row).
    """
    from fakes import NEGATIVE_ONLY_TESTS
    from testroute.llm.ollama import ollama_seed
    from testroute.trace import read_jsonl

    subject = load_subject(DATA_DIR / "classify.py", "classify", function_id="tiny_classify")
    config = load_config("experiment_main.yaml")

    def run(folder: str, trial: int) -> tuple[list[int], list[int]]:
        sent: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/tags":
                return httpx.Response(200, json={"models": [{"name": "qwen2.5-coder:7b", "digest": "d"}]})
            sent.append(json.loads(request.content)["options"]["seed"])
            return httpx.Response(200, json={"model": "qwen2.5-coder:7b", "done_reason": "stop",
                                             "message": {"content": NEGATIVE_ONLY_TESTS}})

        provider = OllamaProvider("http://ollama.test", "qwen2.5-coder:7b", transport=httpx.MockTransport(handler))
        store = ResultsStore(tmp_path / folder)
        RunController(subject=subject, condition=parse_condition("L-F4"), trial=trial, providers={"local": provider},
                      analyzer=ScriptedAnalyzer(store), store=store, config=config).execute()
        return sent, [c["seed"] for c in read_jsonl(store.dir / "calls.jsonl")]

    first_sent, first_recorded = run("a", 1)
    rerun_sent, rerun_recorded = run("b", 1)
    trial2_sent, _ = run("c", 2)
    assert first_sent == first_recorded == [1001, 1002, 1003, 1004]
    assert len(set(first_sent)) == 4
    assert rerun_sent == rerun_recorded == first_sent
    assert trial2_sent == [2001, 2002, 2003, 2004]
    assert ollama_seed(3, 2) == 3002
