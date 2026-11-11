"""Experimental conditions and the model routing policy.

Component: C4 (Model Router).

Responsibility:
    * :func:`parse_condition` turns a condition name (L-F0 ... C-F4, ESC) into
      a :class:`Condition`: routing policy, feedback mode, enabled feedback
      types and call budget (spec Section 2 and D2 Table 5).
    * :class:`Router` picks ``local`` or ``cloud`` for each call. Local and
      cloud policies always use their one model. ESC sends calls 1 and 2 to
      the local model and, only if the quality target is still not met, calls
      3 and 4 to the cloud model (spec C4, Section 9.2). The controller stops
      a run as soon as the target is met, so the router is only asked for a
      call that is actually needed.
    * The router never falls back silently to another model: it only names a
      role, and if the provider for that role fails the run is an INFRA
      failure (D2 Table 4).

Receives: a condition name; per call, the call number.
Produces: :class:`Condition`; the role (``local`` or ``cloud``) per call and
    the escalation flag and reason.
Talks to: used by the controller (C10) and later the experiment runner (C1).
Spec sections: 2 (conditions, call budget), 5 (C4), 9.2.
Feedback IDs: FB1 (condition tiers use these names), FB7 (ESC policy),
    FB12 (identical call budgets).
"""

from __future__ import annotations

from dataclasses import dataclass

from testroute.prompts import COV, EXEC, MUT

LOCAL = "local"
CLOUD = "cloud"

POLICY_LOCAL = "local"
POLICY_CLOUD = "cloud"
POLICY_ESCALATION = "escalation"

# Feedback types per mode (D2 Section 7: execution feedback is part of every
# feedback mode, because a file that does not run cannot be measured).
FEEDBACK_TYPES: dict[str, frozenset[str]] = {
    "F0": frozenset(),
    "F1": frozenset({EXEC}),
    "F2": frozenset({EXEC, COV}),
    "F3": frozenset({EXEC, MUT}),
    "F4": frozenset({EXEC, COV, MUT}),
}


class ConditionError(ValueError):
    """Raised for an unknown condition name."""


@dataclass(frozen=True)
class Condition:
    """One experimental condition.

    Attributes:
        name: Condition name, for example ``C-F4`` or ``ESC``.
        policy: local, cloud or escalation.
        feedback_mode: F0 to F4 (ESC uses F4).
        feedback_types: Enabled T2 blocks.
        max_calls: Call budget (1 for F0, otherwise 4).
        local_calls: For ESC, calls given to the local model first.
    """

    name: str
    policy: str
    feedback_mode: str
    feedback_types: frozenset[str]
    max_calls: int
    local_calls: int = 0

    @property
    def uses_local(self) -> bool:
        """True when the condition may call the local model (needs Ollama)."""
        return self.policy in (POLICY_LOCAL, POLICY_ESCALATION)

    @property
    def uses_cloud(self) -> bool:
        """True when the condition may call the cloud model (needs the key)."""
        return self.policy in (POLICY_CLOUD, POLICY_ESCALATION)


def parse_condition(name: str, budget: dict | None = None) -> Condition:
    """Build a :class:`Condition` from its name.

    Args:
        name: ``L-F0`` to ``L-F4``, ``C-F0`` to ``C-F4``, or ``ESC``
            (case-insensitive).
        budget: The ``call_budget`` block of config/models.yaml. Defaults to
            the spec values (F0 1 call, feedback 4 calls, ESC 2 local + 2
            cloud).

    Returns:
        The condition.

    Raises:
        ConditionError: for an unknown name.
    """
    budget = budget or {"F0": 1, "feedback": 4, "esc_local_calls": 2, "esc_cloud_calls": 2}
    key = name.strip().upper()
    if key == "ESC":
        local, cloud = int(budget["esc_local_calls"]), int(budget["esc_cloud_calls"])
        return Condition("ESC", POLICY_ESCALATION, "F4", FEEDBACK_TYPES["F4"], local + cloud, local)
    prefix, _, mode = key.partition("-")
    if prefix not in ("L", "C") or mode not in FEEDBACK_TYPES:
        raise ConditionError(f"unknown condition '{name}'; use L-F0..L-F4, C-F0..C-F4 or ESC")
    calls = int(budget["F0"]) if mode == "F0" else int(budget["feedback"])
    policy = POLICY_LOCAL if prefix == "L" else POLICY_CLOUD
    return Condition(key, policy, mode, FEEDBACK_TYPES[mode], calls)


class Router:
    """Chooses the model role for each call of one run."""

    def __init__(self, condition: Condition) -> None:
        """Args: condition: the run's condition."""
        self.condition = condition

    def role_for_call(self, call_no: int) -> str:
        """Return ``local`` or ``cloud`` for call ``call_no`` (1-based).

        Raises:
            ValueError: when ``call_no`` exceeds the condition's budget.
        """
        if not 1 <= call_no <= self.condition.max_calls:
            raise ValueError(f"call {call_no} is outside the budget of {self.condition.name}")
        if self.condition.policy == POLICY_LOCAL:
            return LOCAL
        if self.condition.policy == POLICY_CLOUD:
            return CLOUD
        return LOCAL if call_no <= self.condition.local_calls else CLOUD

    def is_escalation(self, call_no: int) -> bool:
        """True for the first cloud call of an ESC run (the escalation point)."""
        return self.condition.policy == POLICY_ESCALATION and call_no == self.condition.local_calls + 1
