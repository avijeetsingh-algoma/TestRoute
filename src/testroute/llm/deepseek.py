"""DeepSeek provider for the cloud model.

Component: C5 (LLM Client), cloud provider.

Responsibility:
    Sends one stateless chat completion to DeepSeek's OpenAI-compatible API
    with the ``openai`` SDK, using spec Section 2's fixed settings: model from
    config/models.yaml (``deepseek-flash``), temperature 0.2, max_tokens 2048,
    no seed, and thinking DISABLED with
    ``extra_body={"thinking": {"type": "disabled"}}``. Thinking is the API
    default and must be off so cost, latency and output format stay
    comparable with the local model (spec Sections 2 and 3).

    Records (spec Section 7): usage with prompt cache hit and miss tokens,
    reasoning tokens when reported (expected 0 with thinking disabled), the
    returned ``model`` and ``system_fingerprint``, finish reason, UTC
    timestamps; the peak flag is derived by the controller from the start
    time and config/pricing.yaml.

    Secrets: the key is passed to the SDK only. Error messages are built from
    the exception type and HTTP status, never from the server's message,
    because an authentication error message can echo part of the key.
    The SDK's own retries are turned off (``max_retries=0``) so the shared
    policy of :func:`call_with_retries` is the only one, as with Ollama.

Receives: :class:`LLMRequest`; the key and base URL from .env.
Produces: :class:`LLMResult` with provider ``deepseek``.
Talks to: the DeepSeek API (network).
Spec sections: 1.3, 2, 3, 5 (C5), 7.
Feedback IDs: FB12 (returned model id and system fingerprint per call).
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import openai

from testroute.llm.base import (
    InfraError,
    LLMProvider,
    LLMRequest,
    LLMResult,
    TransientError,
    call_with_retries,
    utc_now,
)

# Sent with every request: thinking mode off (spec Section 2, locked).
THINKING_DISABLED = {"thinking": {"type": "disabled"}}


class DeepSeekProvider(LLMProvider):
    """Cloud model on the DeepSeek API."""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        *,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
        timeout_s: float = 60.0,
        retry: dict[str, Any] | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Args:
            api_key: DeepSeek key from .env. Never logged.
            base_url: DEEPSEEK_BASE_URL from .env.
            model: Model id from config/models.yaml.
            temperature, max_output_tokens: Spec 2 values.
            timeout_s: Request timeout (spec C5: 60 s).
            retry: The ``retries`` block of models.yaml.
            http_client: Optional httpx client (tests use a mock transport).

        Raises:
            InfraError: when the key is empty, so a run never starts a cloud
                call that is bound to fail.
        """
        if not api_key:
            raise InfraError("DEEPSEEK_API_KEY is empty; paste the key into .env")
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.retry = retry or {}
        self.client = openai.OpenAI(
            api_key=api_key, base_url=base_url, timeout=timeout_s, max_retries=0, http_client=http_client
        )

    def __repr__(self) -> str:
        # The default repr could expose the client and its key in a traceback.
        return f"DeepSeekProvider(model={self.model!r})"

    def complete(self, request: LLMRequest) -> LLMResult:
        """Send one chat completion (see module docstring).

        Raises:
            InfraError: after retries, or immediately for a permanent error
                such as a rejected key or an unknown model.
        """
        started = utc_now()
        start = time.perf_counter()
        response, retries = call_with_retries(
            lambda: self._send(request),
            max_retries=int(self.retry.get("max_retries", 3)),
            backoff_base_s=float(self.retry.get("backoff_base_s", 2)),
            backoff_factor=float(self.retry.get("backoff_factor", 2)),
        )
        latency = time.perf_counter() - start
        choice = response.choices[0]
        usage = response.usage
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        # DeepSeek adds its cache counters as extra usage fields.
        hit = int(_extra(usage, "prompt_cache_hit_tokens") or 0)
        miss = _extra(usage, "prompt_cache_miss_tokens")
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = int(getattr(details, "reasoning_tokens", 0) or 0) if details is not None else 0
        return LLMResult(
            text=choice.message.content or "",
            provider=self.name,
            model=str(response.model or self.model),
            fingerprint=str(response.system_fingerprint or ""),
            input_tokens=prompt_tokens,
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            cache_hit_tokens=hit,
            cache_miss_tokens=int(miss) if miss is not None else prompt_tokens - hit,
            reasoning_tokens=reasoning,
            latency_s=latency,
            finish_reason=str(choice.finish_reason or ""),
            retries=retries,
            started_utc=started,
            ended_utc=utc_now(),
        )

    def _send(self, request: LLMRequest) -> Any:
        """One API attempt, mapping SDK exceptions to Transient or Infra errors."""
        try:
            return self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                temperature=self.temperature,
                max_tokens=self.max_output_tokens,
                stream=False,
                extra_body=THINKING_DISABLED,
            )
        except openai.APIConnectionError as exc:  # includes APITimeoutError
            raise TransientError(f"DeepSeek request failed ({type(exc).__name__})") from None
        except openai.RateLimitError as exc:
            raise TransientError("DeepSeek HTTP 429", _retry_after(exc.response)) from None
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientError(f"DeepSeek HTTP {exc.status_code}") from None
            raise InfraError(f"DeepSeek HTTP {exc.status_code} ({type(exc).__name__})") from None


def _extra(usage: Any, name: str) -> Any:
    """Read a provider-specific usage field kept by the SDK as an extra."""
    value = getattr(usage, name, None)
    if value is None and getattr(usage, "model_extra", None):
        value = usage.model_extra.get(name)
    return value


def _retry_after(response: httpx.Response | None) -> float | None:
    """Seconds from a Retry-After header, if present and numeric."""
    if response is None:
        return None
    try:
        return float(response.headers.get("retry-after"))
    except (TypeError, ValueError):
        return None
