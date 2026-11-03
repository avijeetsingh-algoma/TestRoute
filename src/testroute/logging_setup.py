"""Human-readable run logs and secret redaction.

Component: C11 (Results Logger), logging part. ``records.py`` is the other
part.

Responsibility:
    * :func:`redact` removes secrets from any text before it is written to a
      log, record, artifact or console: the DeepSeek key read from ``.env``,
      plus anything shaped like an API key (``sk-`` followed by 16 or more
      key characters, or a ``Bearer`` token). Spec rule 1.3: the key never
      appears in logs, artifacts, results or exception messages.
    * :class:`RunLog` writes ``run.log`` in the format of spec Section 13:
      ``[HH:MM:SS] <component> <message>``, one line per step, and can echo
      the same lines to the console.

Receives: text and messages from every component.
Produces: redacted text; ``run.log`` lines.
Talks to: ``settings`` (to learn the key value to redact); used by C10,
    C11 and later C1.
Spec sections: 1.3, 11 (C11), 13 (run.log format).
Feedback IDs: none directly.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from testroute.settings import load_env_settings

REDACTED = "[REDACTED]"

# Generic key shapes, caught even if the value differs from the one in .env
# (for example a key pasted into a test by a model, or an old key).
_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),
)


@lru_cache(maxsize=1)
def _configured_secrets() -> tuple[str, ...]:
    """Secret values from .env, read once per process.

    Very short values are ignored: replacing a 1 to 7 character string
    everywhere would mangle ordinary text and such a value is not a real key.
    """
    key = load_env_settings().deepseek_api_key
    return (key,) if len(key) >= 8 else ()


def redact(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """Return ``text`` with every known or key-shaped secret replaced.

    Args:
        text: Any text about to be written somewhere.
        extra_secrets: Additional literal values to remove (used by tests).

    Returns:
        The redacted text.

    Side effects:
        Reads .env on first use (cached).
    """
    for secret in _configured_secrets() + tuple(s for s in extra_secrets if s):
        text = text.replace(secret, REDACTED)
    for pattern in _KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RunLog:
    """Appends ``[HH:MM:SS] <component> <message>`` lines to a run.log file."""

    def __init__(self, path: Path | None, echo: bool = False) -> None:
        """Args:
            path: File to append to; None logs to the console only.
            echo: Also print each line to standard output.

        Side effects:
            Creates the parent folder of ``path``.
        """
        self.path = path
        self.echo = echo
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, component: str, message: str) -> None:
        """Write one redacted log line.

        Args:
            component: Component id such as ``C5`` or ``C10``.
            message: One-line description of the step. Newlines are replaced
                so one step always stays one line.
        """
        line = redact(f"[{datetime.now().strftime('%H:%M:%S')}] {component} {message}".replace("\n", " | "))
        if self.path is not None:
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")
        if self.echo:
            print(line, file=sys.stdout, flush=True)
