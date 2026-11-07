"""Extracts the generated test file from a model response.

Component: C6 (Response Parser).

Responsibility:
    Implements validation check V1 (extraction) with the rules of D2 Table 4,
    row "Markdown or prose around the code", as fixed by spec C6:
    1. Take fenced Python code blocks (```python, ```py, ```python3 or a bare
       ```). An unclosed final fence, typical of a reply cut off at the token
       limit, runs to the end of the text.
    2. If there are several, take the largest one that defines a ``test_``
       function; if none defines one, take the largest block.
    3. If there is no fence, accept the whole text only if it parses as Python.
    4. Otherwise the result is NO_CODE.
    It also reports what was stripped (prose before and after, other blocks),
    which is written to the trace as ``06_C6_parse_report.json``.

    The parser never classifies TRUNCATED itself: the controller (C10) maps
    a V1 or V2 failure to TRUNCATED when the provider's finish reason was
    "length" (spec Section 8).

Receives: raw response text.
Produces: :class:`ParseResult` (code or None, plus a report dict).
Talks to: called by the controller (C10) after C5; its output goes to C7.
Spec sections: 5 (C6), 8 (V1, NO_CODE).
Feedback IDs: none directly.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# Info strings accepted as Python fences. A bare fence counts too, because
# small models often omit the language tag.
PYTHON_FENCE_TAGS = {"python", "py", "python3", ""}

# Opening fence: three or more backticks, optional language tag, end of line.
_FENCE_OPEN = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[ \t]*$", re.MULTILINE)
_FENCE_CLOSE = re.compile(r"^[ \t]*```[ \t]*$", re.MULTILINE)
_DEFINES_TEST = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)

ERROR_NO_CODE = "NO_CODE"


@dataclass
class CodeBlock:
    """One fenced block found in the response.

    Attributes:
        tag: Language tag after the fence ("" for a bare fence).
        code: Text between the fences.
        start: Character offset of the opening fence.
        end: Character offset just after the closing fence (or end of text).
        closed: False when the block ran to the end of the text.
    """

    tag: str
    code: str
    start: int
    end: int
    closed: bool


@dataclass
class ParseResult:
    """Outcome of extraction (V1).

    Attributes:
        code: The extracted test file, or None for NO_CODE.
        error_type: "NO_CODE" when nothing usable was found, else None.
        report: What was found and stripped, for the trace.
    """

    code: str | None
    error_type: str | None
    report: dict = field(default_factory=dict)


def find_code_blocks(text: str) -> list[CodeBlock]:
    """Find every fenced code block, in order of appearance.

    Args:
        text: Raw response text.

    Returns:
        All fenced blocks, Python or not.
    """
    blocks: list[CodeBlock] = []
    position = 0
    while True:
        opening = _FENCE_OPEN.search(text, position)
        if opening is None:
            break
        body_start = opening.end() + 1  # skip the newline after the fence
        closing = _FENCE_CLOSE.search(text, body_start)
        if closing is None:
            blocks.append(CodeBlock(opening.group(1).lower(), text[body_start:], opening.start(), len(text), False))
            break
        blocks.append(
            CodeBlock(opening.group(1).lower(), text[body_start : closing.start()], opening.start(), closing.end(), True)
        )
        position = closing.end()
    return blocks


def _parses(code: str) -> bool:
    """True when ``code`` is syntactically valid Python."""
    try:
        ast.parse(code)
    except (SyntaxError, ValueError):
        return False
    return True


def parse_response(text: str) -> ParseResult:
    """Extract the test file from a response (validation check V1).

    Args:
        text: Raw response text from the provider.

    Returns:
        A :class:`ParseResult`. ``code`` ends with exactly one newline.

    Raises:
        Nothing.

    Side effects:
        None.
    """
    report: dict = {"response_chars": len(text), "blocks_found": 0, "python_blocks": 0}
    if not text.strip():
        report["rule"] = "empty response"
        return ParseResult(None, ERROR_NO_CODE, report)

    blocks = find_code_blocks(text)
    python_blocks = [b for b in blocks if b.tag in PYTHON_FENCE_TAGS]
    report["blocks_found"] = len(blocks)
    report["python_blocks"] = len(python_blocks)

    if python_blocks:
        with_tests = [b for b in python_blocks if _DEFINES_TEST.search(b.code)]
        pool = with_tests or python_blocks
        chosen = max(pool, key=lambda b: len(b.code))
        code = chosen.code.rstrip() + "\n"
        if not code.strip():
            report["rule"] = "only empty code blocks"
            return ParseResult(None, ERROR_NO_CODE, report)
        report.update(
            rule="largest fenced block defining a test" if with_tests else "largest fenced block (none defines a test)",
            chosen_block_index=python_blocks.index(chosen),
            chosen_block_closed=chosen.closed,
            stripped_prefix_chars=len(text[: chosen.start].strip()),
            stripped_suffix_chars=len(text[chosen.end :].strip()),
            other_blocks_dropped=len(blocks) - 1,
        )
        return ParseResult(code, None, report)

    if _parses(text):
        report["rule"] = "no fence; whole text parses as Python"
        return ParseResult(text.rstrip() + "\n", None, report)

    report["rule"] = "no Python fence and the text does not parse"
    return ParseResult(None, ERROR_NO_CODE, report)
