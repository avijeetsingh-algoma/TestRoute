"""Deterministic fake provider for offline tests.

Component: C5 (LLM Client), fake provider.

Responsibility:
    Returns scripted responses without any network access, so the whole
    pipeline can be tested offline (spec Sections 7 and 14). A script is a
    list of responses served in order; each item is either response text or
    a function of the request (useful for answers that depend on the
    module name). :func:`canned` builds the malformed cases spec Section 7
    lists: no code, prose around code, truncated, syntax error, unsafe
    import, failing assertion, plus a well-formed reply.

    Token counts are estimated from character counts so cost code can be
    exercised; latency is the real (tiny) elapsed time.

Receives: :class:`LLMRequest`.
Produces: :class:`LLMResult` with ``provider`` ``fake``.
Talks to: used by tests through the controller (C10).
Spec sections: 7 (fake provider), 14 (offline tests).
Feedback IDs: none directly.
"""

from __future__ import annotations

import time
from typing import Callable, Union

from testroute.llm.base import FINISH_LENGTH, InfraError, LLMProvider, LLMRequest, LLMResult, utc_now

# A scripted reply: fixed text, or a function computing text from the request.
# A reply may also be a (text, finish_reason) pair, or an InfraError instance
# to simulate a failing service.
Reply = Union[str, tuple[str, str], Callable[[LLMRequest], str], InfraError]


class FakeProvider(LLMProvider):
    """Serves scripted replies in order.

    Attributes:
        requests: Every request received, for assertions in tests.
    """

    name = "fake"

    def __init__(self, script: list[Reply], model: str = "fake-model", fingerprint: str = "fake-fp") -> None:
        """Args:
            script: Replies served one per call, in order.
            model: Model id written to the results.
            fingerprint: Value written to the fingerprint field.
        """
        self.script = list(script)
        self.model = model
        self.fingerprint = fingerprint
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResult:
        """Return the next scripted reply.

        Raises:
            InfraError: when the script is exhausted or the scripted item is
                an InfraError.
        """
        started = utc_now()
        start = time.perf_counter()
        self.requests.append(request)
        if not self.script:
            raise InfraError("fake provider script exhausted")
        item = self.script.pop(0)
        if isinstance(item, InfraError):
            raise item
        finish = "stop"
        if isinstance(item, tuple):
            text, finish = item
        elif callable(item):
            text = item(request)
        else:
            text = item
        prompt_tokens = (len(request.system) + len(request.user)) // 4 + 1
        return LLMResult(
            text=text,
            provider=self.name,
            model=self.model,
            fingerprint=self.fingerprint,
            input_tokens=prompt_tokens,
            output_tokens=len(text) // 4 + 1,
            cache_hit_tokens=0,
            cache_miss_tokens=prompt_tokens,
            latency_s=time.perf_counter() - start,
            finish_reason=finish,
            started_utc=started,
            ended_utc=utc_now(),
        )


def canned(kind: str, module: str, entry: str) -> Reply:
    """Build one of the standard malformed or valid replies.

    Args:
        kind: ``good``, ``no_code``, ``prose_around``, ``truncated``,
            ``syntax_error``, ``unsafe_import`` or ``failing_assertion``.
        module: Module name of the subject.
        entry: Entry point of the subject.

    Returns:
        A script item for :class:`FakeProvider`.

    Raises:
        KeyError: for an unknown kind.
    """
    header = f"from {module} import {entry}\n\n"
    replies: dict[str, Reply] = {
        "good": f"```python\n{header}def test_runs():\n    assert {entry} is not None\n```\n",
        "no_code": "I am sorry, I cannot help with that request.",
        "prose_around": (
            "Here are the tests you asked for:\n\n"
            f"```python\n{header}def test_runs():\n    assert {entry} is not None\n```\n\n"
            "These tests cover the main behaviour."
        ),
        "truncated": (f"```python\n{header}def test_runs():\n    assert {entry}(", FINISH_LENGTH),
        "syntax_error": f"```python\n{header}def test_runs(:\n    assert True\n```\n",
        "unsafe_import": f"```python\nimport os\n{header}def test_runs():\n    os.remove('x')\n    assert True\n```\n",
        "failing_assertion": f"```python\n{header}def test_wrong():\n    assert {entry} is None\n```\n",
    }
    return replies[kind]
