"""Replay provider: re-serves recorded responses.

Component: C5 (LLM Client), replay provider.

Responsibility:
    Lets a grader see local-model and ESC runs without installing Ollama, and
    lets the demo run offline parts (spec Sections 3 and 7). Responses are
    looked up in an existing results folder by the key
    ``(function_id, condition, trial, call_no)`` from its ``calls.jsonl``, and
    the request's prompt SHA-256 must equal the recorded one: if the prompt
    differs (because templates, the subject or earlier responses changed), the
    recorded response does not belong to this prompt and an error is raised
    instead of silently serving it.

    Results carry ``provider: replay`` and the controller writes
    ``[REPLAY]`` in run.log. Token counts, latency and local compute time are
    copied from the original call, so cost and time analysis of a replayed
    run shows what the live run measured; the provider label makes clear
    nothing was generated now.

Receives: :class:`LLMRequest`; a results folder to replay from.
Produces: :class:`LLMResult` with provider ``replay``.
Talks to: reads ``calls.jsonl`` and response artifacts of that folder.
Spec sections: 3 (replay row), 7.
Feedback IDs: FB9 (runs fully without manual steps even without Ollama).
"""

from __future__ import annotations

from pathlib import Path

from testroute.llm.base import InfraError, LLMProvider, LLMRequest, LLMResult, utc_now
from testroute.trace import read_jsonl


class ReplayError(InfraError):
    """No recorded response matches the request, or its prompt hash differs.

    Subclass of InfraError so the run is recorded as not completed (never as
    a model failure), with this message as the reason.
    """


class ReplayProvider(LLMProvider):
    """Serves recorded responses from ``source_dir``."""

    name = "replay"

    def __init__(self, source_dir: Path) -> None:
        """Index the recorded calls.

        Args:
            source_dir: Results folder with ``calls.jsonl`` and artifacts.

        Side effects:
            Reads ``calls.jsonl``.
        """
        self.source_dir = source_dir
        self.index: dict[tuple[str, str, int, int], dict] = {}
        for call in read_jsonl(source_dir / "calls.jsonl"):
            # Only successful, originally generated calls can be replayed.
            if call.get("error_class") == "INFRA" or call.get("provider") == "replay" or not call.get("response_path"):
                continue
            key = (call["function_id"], call["condition"], int(call["trial"]), int(call["call_no"]))
            self.index[key] = call

    def complete(self, request: LLMRequest) -> LLMResult:
        """Return the recorded response for this request.

        Raises:
            ReplayError: when nothing was recorded for the key, or the
                recorded prompt hash differs from this request's.
        """
        key = (request.function_id, request.condition, request.trial, request.call_no)
        call = self.index.get(key)
        if call is None:
            raise ReplayError(f"no recorded response for {key} in {self.source_dir}")
        if call.get("prompt_sha256") != request.prompt_sha256:
            raise ReplayError(f"prompt hash differs from the recording for {key}; the response cannot be replayed")
        text = (self.source_dir / call["response_path"]).read_text(encoding="utf-8")
        now = utc_now()
        return LLMResult(
            text=text,
            provider=self.name,
            model=str(call.get("model", "")),
            fingerprint=str(call.get("fingerprint", "")),
            input_tokens=int(call.get("input_tokens") or 0),
            output_tokens=int(call.get("output_tokens") or 0),
            cache_hit_tokens=int(call.get("cache_hit_tokens") or 0),
            cache_miss_tokens=int(call.get("cache_miss_tokens") or 0),
            reasoning_tokens=int(call.get("reasoning_tokens") or 0),
            latency_s=float(call.get("latency_s") or 0),
            local_eval_s=float(call.get("local_eval_s") or 0),
            finish_reason=str(call.get("finish_reason", "stop")),
            retries=0,
            seed=call.get("seed"),
            # The original start time is kept so the peak flag and the
            # time-of-day cost of a replayed cloud call match the live call.
            started_utc=str(call.get("started_utc") or now),
            ended_utc=now,
        )
