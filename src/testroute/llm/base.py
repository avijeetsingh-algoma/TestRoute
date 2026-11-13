"""Provider interface shared by every LLM backend.

Component: C5 (LLM Client), interface.

Responsibility:
    Defines one request type, one result type and one abstract provider, so
    the controller (C10) treats Ollama, DeepSeek, replay and fake providers
    identically (spec C5: "behind one interface returning text, tokens,
    latency, finish reason, model id, fingerprint, retries").
    INFRA failures (network, server, rate limit after retries) are raised as
    :class:`InfraError`; they are never recorded as model failures (D2
    Table 4) and the run is re-queued by C1.

Receives: :class:`LLMRequest` built by the controller from a C3 prompt and
    the C4 role.
Produces: :class:`LLMResult`, or raises :class:`InfraError`. Also provides
    :func:`call_with_retries`, the shared retry policy of spec C5 (3 retries,
    exponential backoff, Retry-After honored for HTTP 429).
Talks to: implemented by ``llm.fake`` (and in milestone 4 ``llm.ollama``,
    ``llm.deepseek``, ``llm.replay``); called by C10; results go to C6 and C11.
Spec sections: 5 (C5), 7, 8 (INFRA class).
Feedback IDs: FB12 (model id, digest or fingerprint per call).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Callable, TypeVar

T = TypeVar("T")

# Finish reason meaning the reply hit the output token limit; the controller
# maps a resulting V1 or V2 failure to TRUNCATED (spec Section 8).
FINISH_LENGTH = "length"


class InfraError(Exception):
    """An infrastructure failure: the call could not be completed.

    Messages must never contain secrets; providers build them from status
    codes and exception type names only.

    Attributes:
        retries: Retries made before giving up.
    """

    def __init__(self, message: str, retries: int = 0) -> None:
        super().__init__(message)
        self.retries = retries


@dataclass(frozen=True)
class LLMRequest:
    """One stateless call: a system message and one user message.

    Attributes:
        run_id: Run the call belongs to.
        call_no: 1-based call number within the run.
        role: ``local`` or ``cloud`` (chosen by C4).
        system: System message (T1_system).
        user: User message (filled T1 or T2).
        prompt_sha256: Hash of (system, user) from C3.
        function_id: Subject id (key for the replay provider).
        condition: Condition name (key for the replay provider).
        trial: Trial number; with ``call_no`` it fixes the Ollama seed
            (1000 x trial + call number, spec Section 2).
    """

    run_id: str
    call_no: int
    role: str
    system: str
    user: str
    prompt_sha256: str
    function_id: str
    condition: str
    trial: int


@dataclass
class LLMResult:
    """What a provider returns for one call.

    Attributes:
        text: Raw response text.
        provider: ``ollama``, ``deepseek``, ``replay`` or ``fake``.
        model: Model id as reported by the service (or configured).
        fingerprint: Ollama model digest or DeepSeek system_fingerprint.
        input_tokens: Prompt tokens as reported.
        output_tokens: Completion tokens as reported.
        cache_hit_tokens: DeepSeek prompt cache hit tokens (0 otherwise).
        cache_miss_tokens: DeepSeek prompt cache miss tokens (input tokens
            when the service does not split them).
        reasoning_tokens: Reasoning tokens if the service reports them. With
            DeepSeek thinking disabled (spec Section 2) this must stay 0;
            recording it makes that checkable.
        latency_s: Wall-clock time of the request including retries.
        local_eval_s: Ollama generation time (total_duration), 0 for cloud.
        finish_reason: ``stop``, ``length`` or the service's value.
        retries: Retries made before success.
        seed: Sampling seed sent with the call, or None when the service
            takes no seed (DeepSeek, spec Section 2).
        started_utc: ISO-8601 UTC start time.
        ended_utc: ISO-8601 UTC end time.
    """

    text: str
    provider: str
    model: str
    fingerprint: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    local_eval_s: float = 0.0
    finish_reason: str = "stop"
    retries: int = 0
    seed: int | None = None
    started_utc: str = ""
    ended_utc: str = ""

    def to_dict(self) -> dict:
        """JSON-ready form (the text included), for trace file 05."""
        return asdict(self)


def utc_now() -> str:
    """Current UTC time in ISO-8601 with seconds precision."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TransientError(Exception):
    """A failure worth retrying: timeout, connection error, HTTP 5xx or 429.

    Attributes:
        retry_after_s: Wait requested by the service (HTTP 429 Retry-After),
            or None to use exponential backoff.
    """

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


def call_with_retries(
    send: Callable[[], T],
    *,
    max_retries: int = 3,
    backoff_base_s: float = 2.0,
    backoff_factor: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[T, int]:
    """Call ``send`` and retry transient failures (spec C5, D2 Table 4).

    Retry k (k = 1, 2, 3) waits ``backoff_base_s * backoff_factor**(k-1)``
    seconds, or the service's Retry-After value for HTTP 429.

    Args:
        send: Performs one attempt; raises TransientError for retryable
            failures and InfraError for permanent ones.
        max_retries: Retries after the first attempt.
        backoff_base_s: First wait in seconds.
        backoff_factor: Growth of the wait per retry.
        sleep: Wait function (replaced in tests).

    Returns:
        (send's result, retries used).

    Raises:
        InfraError: when every attempt failed transiently, or on a permanent
            failure; ``retries`` tells how many retries were made.
    """
    retries = 0
    while True:
        try:
            return send(), retries
        except TransientError as exc:
            if retries >= max_retries:
                raise InfraError(f"{exc} (gave up after {retries} retries)", retries) from None
            wait = exc.retry_after_s if exc.retry_after_s is not None else backoff_base_s * backoff_factor**retries
            retries += 1
            sleep(wait)
        except InfraError as exc:
            exc.retries = retries
            raise


class LLMProvider(ABC):
    """Abstract provider. Subclasses implement :meth:`complete`."""

    #: Short provider name written to records (``provider`` field).
    name: str = "abstract"

    @abstractmethod
    def complete(self, request: LLMRequest) -> LLMResult:
        """Send one request and return the result.

        Raises:
            InfraError: when the call cannot be completed after retries.
        """
