"""Builds the T1 (generation) and T2 (feedback) prompts.

Component: C3 (Prompt Builder).

Responsibility:
    Fills the D2 templates in ``prompts/`` exactly as written, following the
    filling rules in ``prompts/README.md``:
    * placeholders are replaced by literal string substitution, never
      ``str.format``, because source code and tests contain braces. All
      placeholders of one template are replaced in a single regex pass, so
      text inserted for one placeholder (for example source code that happens
      to contain ``{ENTRY_POINT}``) is never substituted again;
    * a T2 block is inserted only when its feedback type is enabled in the
      condition and it has something to report, otherwise its placeholder
      becomes an empty string;
    * every call sends the T1 system message plus one user message.
    It also estimates the prompt size and, when the estimate exceeds the
    context budget, shortens the feedback in the fixed order of spec C3 and
    D2 Table 4: fewer mutants, then shorter error messages, then fewer
    uncovered lines. Every step taken is recorded with the prompt.

    The template version is a SHA-256 over all seven template files, stored
    with every call (spec Section 7).

    The only rendering choice not fixed by the README: coverage.py reports a
    branch that leaves the function as a jump to a negative line number. Such
    branches are written ``branch from line {N} to exit is never taken``
    because there is no line {M} to name.

Receives: a :class:`Subject`, and for T2 the current test file and a
    :class:`FeedbackBundle` built by the controller (C10).
Produces: :class:`Prompt` (system text, user text, template id and hash,
    prompt SHA-256, size estimate, shortening steps).
Talks to: reads ``prompts/``; called by C10; its output goes to C4 and C5.
Spec sections: 5 (C3), 7 (templates, feedback modes, at most 5 mutants).
Feedback IDs: FB11 (only feedback-set mutants can be rendered: the bundle
    holds MutantInfo taken from a FeedbackMutationView).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from testroute import paths
from testroute.mutation.results import SET_FEEDBACK, HeldOutLeakError, MutantInfo
from testroute.subjects import Subject

# Template files (prompts/README.md). The order fixes the template-set hash.
TEMPLATE_FILES: tuple[str, ...] = (
    "T1_system.txt",
    "T1_generation.txt",
    "T2_feedback.txt",
    "T2_block_exec.txt",
    "T2_block_cov.txt",
    "T2_block_mut.txt",
    "T2_block_nocode.txt",
)

# Feedback types (spec Section 7 / D2 Section 7).
EXEC = "EXEC"
COV = "COV"
MUT = "MUT"

# Placeholder text when no attempt has produced code yet (prompts/README.md).
NO_TEST_FILE = "# no test file was returned"

# Size estimate: characters per token. Code tokenizes at roughly 3 to 4
# characters per token for both models' tokenizers; 3 is the cautious end, so
# the estimate overstates rather than understates the size.
CHARS_PER_TOKEN = 3.0

# Shortening limits for error messages, tried in this order (spec C3 step 2).
MESSAGE_LIMITS: tuple[int, ...] = (120, 40)

_PLACEHOLDER = re.compile(r"\{([A-Z_]+)\}")


class PromptError(Exception):
    """Raised when a template file is missing or a placeholder has no value."""


@dataclass(frozen=True)
class FailureLine:
    """One line of the EXEC block: ``{TEST_NAME}: {ERROR_TYPE}: {ERROR_MESSAGE}``."""

    test_name: str
    error_type: str
    message: str


@dataclass(frozen=True)
class FeedbackBundle:
    """What the controller (C10) wants reported in the next T2 prompt.

    All lists are already restricted to the current test file. The controller
    fills only the parts enabled in the condition; C3 still checks the
    condition so a disabled type can never appear.

    Attributes:
        nocode_reason: Error type of the previous reply when it had no usable
            test file (for example NO_CODE or TRUNCATED), else None.
        failures: EXEC lines, one per failing test or one for the whole file.
        uncovered_lines: (line number, source line) never executed.
        missed_branches: (from line, to line) arcs never taken.
        surviving_mutants: Surviving feedback-set mutants in the seeded prompt
            order. Held-out mutants are rejected (FB11).
    """

    nocode_reason: str | None = None
    failures: tuple[FailureLine, ...] = ()
    uncovered_lines: tuple[tuple[int, str], ...] = ()
    missed_branches: tuple[tuple[int, int], ...] = ()
    surviving_mutants: tuple[MutantInfo, ...] = ()

    def __post_init__(self) -> None:
        leaked = [m.mutant_id for m in self.surviving_mutants if m.set_label != SET_FEEDBACK]
        if leaked:
            raise HeldOutLeakError(f"held-out mutants in a feedback bundle: {leaked}")

    def to_dict(self) -> dict:
        """JSON-ready form, written as attemptN_feedback.json and trace file 11."""
        return asdict(self)


@dataclass(frozen=True)
class Prompt:
    """A filled prompt ready for the router and the LLM client.

    Attributes:
        template_id: "T1" or "T2".
        template_hash: SHA-256 of the whole template set.
        system: System message.
        user: User message.
        sha256: SHA-256 of the JSON form of (system, user); used by the
            replay provider to verify that a recorded response matches.
        estimated_tokens: Size estimate of system plus user message.
        budget_tokens: Input token budget the estimate was compared with.
        shortening: Shortening steps applied, in order (empty if none).
        over_budget: True when the prompt is still over budget after every
            shortening step; it is sent anyway and the flag is recorded.
        blocks: Which T2 blocks were included.
    """

    template_id: str
    template_hash: str
    system: str
    user: str
    sha256: str
    estimated_tokens: int
    budget_tokens: int
    shortening: tuple[str, ...] = ()
    over_budget: bool = False
    blocks: tuple[str, ...] = ()

    def request_dict(self) -> dict:
        """Metadata of the prompt (without the texts), for records and trace 02."""
        data = asdict(self)
        data.pop("system")
        data.pop("user")
        return data


def prompt_sha256(system: str, user: str) -> str:
    """SHA-256 of a (system, user) pair, stable across platforms."""
    payload = json.dumps({"system": system, "user": user}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def estimate_tokens(text: str) -> int:
    """Rough token count of ``text`` (see CHARS_PER_TOKEN)."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


def fill(template: str, values: dict[str, str]) -> str:
    """Replace ``{NAME}`` placeholders by literal text in one pass.

    Args:
        template: Template text.
        values: Placeholder name to replacement text.

    Returns:
        The filled text.

    Raises:
        PromptError: if the template uses a placeholder missing from
            ``values``; a silently unfilled placeholder would reach the model.
    """

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise PromptError(f"no value for placeholder {{{name}}}")
        return values[name]

    return _PLACEHOLDER.sub(_sub, template)


class PromptBuilder:
    """Loads the templates once and fills them for each call."""

    def __init__(self, prompts_dir: Path | None = None, budget_tokens: int = 8192 - 2048) -> None:
        """Read every template file.

        Args:
            prompts_dir: Folder with the template files; default ``prompts/``.
            budget_tokens: Input token budget. Default: the Ollama context of
                8192 tokens minus the 2048 tokens reserved for output, so a
                prompt within budget always leaves room for a full reply.

        Raises:
            PromptError: if a template file is missing.

        Side effects:
            Reads the seven template files.
        """
        folder = prompts_dir if prompts_dir is not None else paths.PROMPTS_DIR
        self.templates: dict[str, str] = {}
        for name in TEMPLATE_FILES:
            path = folder / name
            if not path.is_file():
                raise PromptError(f"prompt template missing: {path}")
            # read_text uses universal newlines, so a CRLF checkout yields the
            # same text (and the same hash) as an LF checkout.
            self.templates[name] = path.read_text(encoding="utf-8")
        digest = hashlib.sha256()
        for name in TEMPLATE_FILES:
            digest.update(name.encode("utf-8") + b"\0" + self.templates[name].encode("utf-8") + b"\0")
        self.template_hash = digest.hexdigest()
        self.budget_tokens = budget_tokens

    @property
    def system(self) -> str:
        """System message of every call. The file's final newline is dropped."""
        return self.templates["T1_system.txt"].rstrip("\n")

    def generation_prompt(self, subject: Subject) -> Prompt:
        """Fill T1 for the first call of a run.

        Args:
            subject: The function under test.

        Returns:
            The filled :class:`Prompt`.
        """
        user = fill(
            self.templates["T1_generation.txt"],
            {
                "MODULE_NAME": subject.module_name,
                "SOURCE_CODE": subject.source.rstrip("\n"),
                "ENTRY_POINT": subject.entry_point,
            },
        ).rstrip("\n")
        return self._prompt("T1", user, (), ())

    def feedback_prompt(
        self,
        subject: Subject,
        current_tests: str | None,
        bundle: FeedbackBundle,
        feedback_types: frozenset[str],
        max_mutants: int = 5,
    ) -> Prompt:
        """Fill T2 for calls 2 to 4, shortening feedback to fit the budget.

        Args:
            subject: The function under test.
            current_tests: The current best test file, or None if no attempt
                produced code yet.
            bundle: Feedback for the current test file.
            feedback_types: Types enabled by the condition (EXEC, COV, MUT).
            max_mutants: Most mutants shown (spec Section 7: 5).

        Returns:
            The filled :class:`Prompt`, with the shortening steps it needed.
        """
        steps: list[str] = []
        # Disabled feedback types are dropped here so they can never appear,
        # whatever the bundle contains.
        bundle = replace(
            bundle,
            failures=bundle.failures if EXEC in feedback_types else (),
            uncovered_lines=bundle.uncovered_lines if COV in feedback_types else (),
            missed_branches=bundle.missed_branches if COV in feedback_types else (),
            surviving_mutants=tuple(bundle.surviving_mutants[:max_mutants]) if MUT in feedback_types else (),
        )
        prompt = self._render_feedback(subject, current_tests, bundle, steps)

        # Step 1: fewer mutants, one at a time, down to a single mutant.
        while self._over(prompt) and len(bundle.surviving_mutants) > 1:
            bundle = replace(bundle, surviving_mutants=bundle.surviving_mutants[:-1])
            steps.append(f"mutants reduced to {len(bundle.surviving_mutants)}")
            prompt = self._render_feedback(subject, current_tests, bundle, steps)
        # Step 2: shorter error messages.
        for limit in MESSAGE_LIMITS:
            if not self._over(prompt) or not bundle.failures:
                break
            bundle = replace(
                bundle, failures=tuple(replace(f, message=f.message[:limit]) for f in bundle.failures)
            )
            steps.append(f"error messages cut to {limit} characters")
            prompt = self._render_feedback(subject, current_tests, bundle, steps)
        # Step 3: fewer uncovered lines. Missed branches go first because each
        # uncovered line usually already implies the branch leading to it.
        removed = 0
        while self._over(prompt) and (bundle.uncovered_lines or bundle.missed_branches):
            if bundle.missed_branches:
                bundle = replace(bundle, missed_branches=bundle.missed_branches[:-1])
            else:
                bundle = replace(bundle, uncovered_lines=bundle.uncovered_lines[:-1])
            removed += 1
            prompt = self._render_feedback(subject, current_tests, bundle, steps + [f"{removed} coverage lines removed"])
        if removed:
            steps.append(f"{removed} coverage lines removed")
            prompt = self._render_feedback(subject, current_tests, bundle, steps)
        return replace(prompt, over_budget=self._over(prompt))

    def _over(self, prompt: Prompt) -> bool:
        return prompt.estimated_tokens > self.budget_tokens

    def _render_feedback(
        self, subject: Subject, current_tests: str | None, bundle: FeedbackBundle, steps: list[str]
    ) -> Prompt:
        """Fill T2 and its blocks for the given (possibly shortened) bundle."""
        blocks: list[str] = []
        values = {"NOCODE_BLOCK": "", "EXEC_BLOCK": "", "COV_BLOCK": "", "MUT_BLOCK": ""}
        if bundle.nocode_reason:
            values["NOCODE_BLOCK"] = fill(self.templates["T2_block_nocode.txt"], {"NOCODE_REASON": bundle.nocode_reason})
            blocks.append("NOCODE")
        if bundle.failures:
            lines = "\n".join(f"{f.test_name}: {f.error_type}: {f.message}" for f in bundle.failures)
            values["EXEC_BLOCK"] = fill(self.templates["T2_block_exec.txt"], {"FAILURE_LINES": lines})
            blocks.append(EXEC)
        if bundle.uncovered_lines or bundle.missed_branches:
            lines = [f"line {n}: {text.strip()}" for n, text in bundle.uncovered_lines]
            lines += [_branch_text(a, b) for a, b in bundle.missed_branches]
            values["COV_BLOCK"] = fill(self.templates["T2_block_cov.txt"], {"UNCOVERED_LINES": "\n".join(lines)})
            blocks.append(COV)
        if bundle.surviving_mutants:
            diffs = "\n".join(
                f"Change {k}:\n-{m.original_line}\n+{m.mutated_line}" for k, m in enumerate(bundle.surviving_mutants, 1)
            )
            values["MUT_BLOCK"] = fill(self.templates["T2_block_mut.txt"], {"MUTANT_DIFFS": diffs})
            blocks.append(MUT)
        values.update(
            MODULE_NAME=subject.module_name,
            SOURCE_CODE=subject.source.rstrip("\n"),
            CURRENT_TESTS=(current_tests or NO_TEST_FILE).rstrip("\n"),
        )
        user = fill(self.templates["T2_feedback.txt"], values).rstrip("\n")
        return self._prompt("T2", user, tuple(steps), tuple(blocks))

    def _prompt(self, template_id: str, user: str, steps: tuple[str, ...], blocks: tuple[str, ...]) -> Prompt:
        system = self.system
        return Prompt(
            template_id=template_id,
            template_hash=self.template_hash,
            system=system,
            user=user,
            sha256=prompt_sha256(system, user),
            estimated_tokens=estimate_tokens(system) + estimate_tokens(user),
            budget_tokens=self.budget_tokens,
            shortening=steps,
            blocks=blocks,
        )


def _branch_text(from_line: int, to_line: int) -> str:
    """Render one missed branch (see module docstring for exit arcs)."""
    if to_line < 0:
        return f"branch from line {from_line} to exit is never taken"
    return f"branch from line {from_line} to line {to_line} is never taken"
