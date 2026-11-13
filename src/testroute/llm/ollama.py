"""Ollama provider for the local model.

Component: C5 (LLM Client), local provider.

Responsibility:
    Sends one stateless chat request to a local Ollama server
    (``POST /api/chat`` with ``stream: false``) using the fixed parameters of
    spec Section 2: temperature 0.2, ``num_predict`` 2048, ``num_ctx`` 8192,
    ``seed`` = 1000 x trial + call number (:func:`ollama_seed`), ``keep_alive``
    30m. Records what spec Section 7
    asks for: ``prompt_eval_count``, ``eval_count``, ``total_duration``,
    ``eval_duration`` and the model digest from ``/api/tags`` (FB12).

    Local compute time (``local_eval_s``) is prompt processing plus
    generation (``prompt_eval_duration + eval_duration``). Model loading
    (``load_duration``) is excluded: ``keep_alive`` keeps the model in memory
    between calls, so loading is a one-off per batch, not a per-call cost.
    The raw durations are still kept in the result for the trace.

    Failures: timeouts, connection errors, HTTP 5xx and 429 are retried by
    :func:`call_with_retries`, then become INFRA errors; any other HTTP error
    (for example 404 for a model that is not pulled) is an immediate INFRA
    error. Never falls back to another model (D2 Table 4).

Receives: :class:`LLMRequest`.
Produces: :class:`LLMResult` with provider ``ollama``.
Talks to: the Ollama HTTP API at ``OLLAMA_HOST`` (from .env).
Spec sections: 2, 5 (C5), 7.
Feedback IDs: FB10 (local compute time), FB12 (model digest per call).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from testroute.llm.base import (
    InfraError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    TransientError,
    call_with_retries,
    utc_now,
)

NANOSECONDS = 1e9
HEALTH_TIMEOUT_S = 5.0


class OllamaProvider(LLMProvider):
    """Local model served by Ollama."""

    name = "ollama"

    def __init__(
        self,
        host: str,
        model: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
        num_ctx: int = 8192,
        keep_alive: str = "30m",
        seed_per_call: bool = True,
        timeout_s: float = 180.0,
        retry: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """Args:
            host: Base URL, for example ``http://localhost:11434``.
            model: Ollama tag, for example ``qwen2.5-coder:7b``.
            temperature, max_output_tokens, num_ctx, keep_alive: Spec 2 values.
            seed_per_call: Send :func:`ollama_seed` as the sampling seed.
            timeout_s: Request timeout (spec C5: 180 s).
            retry: The ``retries`` block of models.yaml.
            transport: Optional httpx transport (tests use a mock).
        """
        self.host = host.rstrip("/")
        self.model = model
        self.options = {"temperature": temperature, "num_predict": max_output_tokens, "num_ctx": num_ctx}
        self.keep_alive = keep_alive
        self.seed_per_call = seed_per_call
        self.retry = retry or {}
        self.client = httpx.Client(timeout=timeout_s, transport=transport)
        self._digest: str | None = None

    def health_check(self) -> str:
        """Return the Ollama version, or raise InfraError if unreachable.

        Run before a batch of local calls (spec Section 7).
        """
        try:
            response = self.client.get(f"{self.host}/api/version", timeout=HEALTH_TIMEOUT_S)
            response.raise_for_status()
            return str(response.json().get("version", "unknown"))
        except (httpx.HTTPError, ValueError) as exc:
            raise InfraError(f"Ollama is not reachable at {self.host} ({type(exc).__name__})") from None

    def digest(self) -> str:
        """Digest of the configured model from ``/api/tags`` (cached).

        Raises:
            InfraError: when the server is unreachable or the model is not
                pulled, since running another model silently is not allowed.
        """
        if self._digest is None:
            try:
                response = self.client.get(f"{self.host}/api/tags", timeout=HEALTH_TIMEOUT_S)
                response.raise_for_status()
                models = response.json().get("models", [])
            except (httpx.HTTPError, ValueError) as exc:
                raise InfraError(f"cannot list Ollama models ({type(exc).__name__})") from None
            match = next((m for m in models if m.get("name") == self.model), None)
            if match is None:
                raise InfraError(f"Ollama model {self.model} is not pulled; run: ollama pull {self.model}")
            self._digest = str(match.get("digest", ""))
        return self._digest

    def complete(self, request: LLMRequest) -> LLMResult:
        """Send one chat request (see module docstring).

        Raises:
            InfraError: after retries, or immediately for a permanent error.
        """
        digest = self.digest()
        options = dict(self.options)
        seed = ollama_seed(request.trial, request.call_no) if self.seed_per_call else None
        if seed is not None:
            options["seed"] = seed
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "stream": False,
            "options": options,
            "keep_alive": self.keep_alive,
        }
        started = utc_now()
        start = time.perf_counter()
        data, retries = call_with_retries(
            lambda: self._post(payload),
            max_retries=int(self.retry.get("max_retries", 3)),
            backoff_base_s=float(self.retry.get("backoff_base_s", 2)),
            backoff_factor=float(self.retry.get("backoff_factor", 2)),
        )
        latency = time.perf_counter() - start
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        compute_ns = int(data.get("prompt_eval_duration") or 0) + int(data.get("eval_duration") or 0)
        return LLMResult(
            text=str((data.get("message") or {}).get("content", "")),
            provider=self.name,
            model=str(data.get("model", self.model)),
            fingerprint=digest,
            input_tokens=prompt_tokens,
            output_tokens=int(data.get("eval_count") or 0),
            cache_hit_tokens=0,
            cache_miss_tokens=prompt_tokens,
            latency_s=latency,
            local_eval_s=compute_ns / NANOSECONDS,
            finish_reason=str(data.get("done_reason") or "stop"),
            retries=retries,
            seed=seed,
            started_utc=started,
            ended_utc=utc_now(),
        )

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """One HTTP attempt, mapping failures to Transient or Infra errors."""
        try:
            response = self.client.post(f"{self.host}/api/chat", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(f"Ollama request failed ({type(exc).__name__})") from None
        if response.status_code == 429 or response.status_code >= 500:
            retry_after = _retry_after(response)
            raise TransientError(f"Ollama HTTP {response.status_code}", retry_after)
        if response.status_code >= 400:
            raise InfraError(f"Ollama HTTP {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError:
            raise TransientError("Ollama returned invalid JSON") from None


def ollama_seed(trial: int, call_no: int) -> int:
    """Sampling seed of one local call: 1000 x trial + call number.

    Spec Sections 2 and 3: a seed per call instead of per trial, so a call
    that repeats an unchanged prompt still gets a fresh sample, while every
    run stays reproducible. Call numbers stay below 1000 (budget is 4).
    """
    return 1000 * int(trial) + int(call_no)


def _retry_after(response: httpx.Response) -> float | None:
    """Seconds from a Retry-After header, if present and numeric."""
    value = response.headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
