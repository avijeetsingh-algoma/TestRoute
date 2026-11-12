"""The mutant set of one function: generation, filtering, cap, split, storage.

Component: C9 (Mutation Analyzer), preparation stage (once per function).

Responsibility:
    :func:`build_mutant_set` runs the preparation steps of spec Section 6 in
    the spec's order:
    1. generate mutants (6.1, ``operators``), numbered in source order;
    2. drop TCE_EQUIVALENT and DUPLICATE mutants (6.2, ``tce``);
    3. apply equivalence results when available (6.4; the differential
       execution and the manual review are built in milestone 5 and passed in
       here as labels);
    4. cap: above ``cap`` non-equivalent mutants, keep a seeded sample
       stratified by operator family and record the dropped ids (6.3);
    5. split the scoring mutants 50/50 into ``feedback`` and ``heldout``,
       stratified by operator family, seed 7 (6.5, FB11).
    It also fixes the seeded order used to pick which surviving feedback
    mutants appear in a prompt (spec Section 7).

    :class:`MutantSet` is stored as ``experiments/mutants/<function_id>.json``
    (spec 6.4 step 4) and never changed after creation.

Receives: a Subject and settings (cap, seeds), optionally equivalence labels.
Produces: :class:`MutantSet`; JSON files.
Talks to: ``operators``, ``tce``, ``results`` (MutantInfo); read by the
    mutant runner and, in milestone 5, written by ``prepare-mutants``.
Spec sections: 6.1 to 6.5, 7.
Feedback IDs: FB2, FB4 (cap), FB11 (fixed seeded split).
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

from testroute.mutation.operators import generate_mutants
from testroute.mutation.results import EQUIVALENT, NOT_REVIEWED, SET_FEEDBACK, SET_HELDOUT, MutantInfo
from testroute.mutation.tce import classify_static

# Differential execution label meaning "only difference is non-termination"
# (spec 6.4); used for timeout_diagnosis in the runner.
DIFF_TIMEOUT_ONLY = "DIFF_TIMEOUT_ONLY"


@dataclass
class Mutant:
    """One mutant and everything decided about it.

    Attributes:
        mutant_id: ``<FAMILY>_<NNNN>``, numbered in source order.
        operator: Operator family.
        line, col: Position of the change.
        original_line, mutated_line: Source line before and after.
        description: Short change description, for example ``< -> <=``.
        diff: Unified diff.
        source: Full mutated module source.
        static_label: "" (kept), TCE_EQUIVALENT or DUPLICATE (6.2).
        duplicate_of: Id of the duplicated mutant for DUPLICATE.
        diff_label: Differential execution label (6.4 step 1), "" until run.
        equivalence: EQUIVALENT, NOT_EQUIVALENT, UNSURE or NOT_REVIEWED.
        capped_out: True when dropped by the mutant cap (6.3).
        set_label: feedback, heldout, or "" for non-scoring mutants.
    """

    mutant_id: str
    operator: str
    line: int
    col: int
    original_line: str
    mutated_line: str
    description: str
    diff: str
    source: str
    static_label: str = ""
    duplicate_of: str = ""
    diff_label: str = ""
    equivalence: str = NOT_REVIEWED
    capped_out: bool = False
    set_label: str = ""

    @property
    def scoring(self) -> bool:
        """Takes part in scoring: survived 6.2 and the cap."""
        return not self.static_label and not self.capped_out

    def info(self) -> MutantInfo:
        """The runtime description used by the runner and the feedback view."""
        return MutantInfo(
            mutant_id=self.mutant_id,
            operator=self.operator,
            line=self.line,
            original_line=self.original_line,
            mutated_line=self.mutated_line,
            set_label=self.set_label,
            equivalence=self.equivalence,
            diff_timeout_only=self.diff_label == DIFF_TIMEOUT_ONLY,
        )


@dataclass
class MutantSet:
    """All mutants of one function plus the settings that produced them."""

    function_id: str
    module_name: str
    entry_point: str
    original_sha256: str
    cap: int
    cap_seed: int
    split_seed: int
    prompt_seed: int
    mutants: list[Mutant] = field(default_factory=list)
    capped_out_ids: list[str] = field(default_factory=list)
    prompt_order: list[str] = field(default_factory=list)

    def scoring(self) -> list[Mutant]:
        """Scoring mutants (both sets), in id order."""
        return [m for m in self.mutants if m.scoring]

    def infos(self) -> dict[str, MutantInfo]:
        """MutantInfo of every scoring mutant, keyed by id."""
        return {m.mutant_id: m.info() for m in self.scoring()}

    def sources(self) -> dict[str, str]:
        """Mutated source of every scoring mutant, keyed by id."""
        return {m.mutant_id: m.source for m in self.scoring()}

    def stage_counts(self) -> dict[str, int]:
        """Counts per preparation stage (feeds equivalence_summary.csv, 6.4 step 5)."""
        counts: dict[str, int] = defaultdict(int)
        counts["generated"] = len(self.mutants)
        for m in self.mutants:
            if m.static_label:
                counts[m.static_label] += 1
            if m.diff_label:
                counts[m.diff_label] += 1
            if m.equivalence != NOT_REVIEWED:
                counts[m.equivalence] += 1
        counts["capped_out"] = len(self.capped_out_ids)
        counts["scoring"] = len(self.scoring())
        counts[SET_FEEDBACK] = sum(1 for m in self.scoring() if m.set_label == SET_FEEDBACK)
        counts[SET_HELDOUT] = sum(1 for m in self.scoring() if m.set_label == SET_HELDOUT)
        return dict(counts)

    def save(self, path: Path) -> None:
        """Write the set as indented JSON (plain text, spec rule 1.4)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")

    @classmethod
    def load(cls, path: Path) -> "MutantSet":
        """Read a set written by :meth:`save`."""
        data = json.loads(path.read_text(encoding="utf-8"))
        mutants = [Mutant(**m) for m in data.pop("mutants")]
        return cls(mutants=mutants, **data)


def _stratified_sample(by_family: dict[str, list[str]], total: int, rng: random.Random) -> set[str]:
    """Pick ``total`` ids, proportional to family size (largest remainder)."""
    size = sum(len(ids) for ids in by_family.values())
    quotas = {f: total * len(ids) / size for f, ids in by_family.items()}
    take = {f: int(q) for f, q in quotas.items()}
    leftover = total - sum(take.values())
    # Largest fractional parts get the remaining places; ties by family name
    # so the result does not depend on dict order.
    for family in sorted(quotas, key=lambda f: (-(quotas[f] - take[f]), f))[:leftover]:
        take[family] += 1
    chosen: set[str] = set()
    for family in sorted(by_family):
        ids = sorted(by_family[family])
        rng.shuffle(ids)
        chosen.update(ids[: take[family]])
    return chosen


def _stratified_split(by_family: dict[str, list[str]], rng: random.Random) -> dict[str, str]:
    """Assign ids alternately to feedback and heldout within shuffled families.

    The alternation continues across families, so the two sets differ in size
    by at most one overall and by at most one within every family.
    """
    labels: dict[str, str] = {}
    toggle = 0
    for family in sorted(by_family):
        ids = sorted(by_family[family])
        rng.shuffle(ids)
        for mutant_id in ids:
            labels[mutant_id] = SET_FEEDBACK if toggle % 2 == 0 else SET_HELDOUT
            toggle += 1
    return labels


def build_mutant_set(
    function_id: str,
    module_name: str,
    entry_point: str,
    source: str,
    *,
    cap: int = 60,
    cap_seed: int = 7,
    split_seed: int = 7,
    prompt_seed: int = 7,
    equivalence: dict[str, tuple[str, str]] | None = None,
) -> MutantSet:
    """Prepare the mutant set of one function (spec 6.1 to 6.5).

    Args:
        function_id: Subject id.
        module_name: Module name (for the runner's file names).
        entry_point: Function under test.
        source: Original module source.
        cap: Maximum non-equivalent scoring mutants (6.3).
        cap_seed: Seed of the cap sample.
        split_seed: Seed of the feedback and held-out split (6.5).
        prompt_seed: Seed of the prompt order of feedback mutants (7).
        equivalence: Optional mutant id -> (diff_label, final review label)
            from the 6.4 pipeline; ids are those this function generates.

    Returns:
        The prepared :class:`MutantSet`.

    Raises:
        ValueError: if the entry point is missing.

    Side effects:
        None (use :meth:`MutantSet.save` to store it).
    """
    generated = sorted(generate_mutants(source, entry_point), key=lambda g: (g.line, g.col, g.operator, g.description))
    mutants: list[Mutant] = []
    for number, g in enumerate(generated, start=1):
        mutants.append(
            Mutant(
                mutant_id=f"{g.operator}_{number:04d}",
                operator=g.operator, line=g.line, col=g.col, original_line=g.original_line,
                mutated_line=g.mutated_line, description=g.description, diff=g.diff, source=g.source,
            )
        )
    for mutant, (label, dup_index) in zip(mutants, classify_static(source, [m.source for m in mutants])):
        mutant.static_label = label
        mutant.duplicate_of = mutants[dup_index].mutant_id if dup_index is not None else ""

    for mutant in mutants:
        if equivalence and mutant.mutant_id in equivalence:
            mutant.diff_label, mutant.equivalence = equivalence[mutant.mutant_id]

    candidates = [m for m in mutants if not m.static_label and m.equivalence != EQUIVALENT]
    capped: list[str] = []
    if len(candidates) > cap:
        by_family: dict[str, list[str]] = defaultdict(list)
        for m in candidates:
            by_family[m.operator].append(m.mutant_id)
        keep = _stratified_sample(by_family, cap, random.Random(cap_seed))
        for m in candidates:
            if m.mutant_id not in keep:
                m.capped_out = True
                capped.append(m.mutant_id)

    scoring_by_family: dict[str, list[str]] = defaultdict(list)
    for m in mutants:
        if m.scoring:
            scoring_by_family[m.operator].append(m.mutant_id)
    labels = _stratified_split(scoring_by_family, random.Random(split_seed))
    for m in mutants:
        m.set_label = labels.get(m.mutant_id, "")

    feedback_ids = sorted(i for i, label in labels.items() if label == SET_FEEDBACK)
    random.Random(prompt_seed).shuffle(feedback_ids)
    return MutantSet(
        function_id=function_id,
        module_name=module_name,
        entry_point=entry_point,
        original_sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        cap=cap,
        cap_seed=cap_seed,
        split_seed=split_seed,
        prompt_seed=prompt_seed,
        mutants=mutants,
        capped_out_ids=capped,
        prompt_order=feedback_ids,
    )
