"""Two of D-18's profile rules that read records beyond a claim's own payload,
re-checked by World as Door checks them at admission: `x-archive-agent` and
`x-archive-target-form`.

A contract module's check is given the claim and what it pins, not the kind
of the claim's agent nor the form of a claim it corrects, so without this
World would project a sensitivity limit asserted by a tool, or a timed meter
correcting an untimed one, that Door refuses. The tables mirror the bundles'
declarations; `world/tests/test_envelope_rules.py` reads every 0.0.10 bundle
and fails when they differ. A fact that cannot be read (an agent whose kind
World does not hold, a pinned claim it does not hold) fails the check.
"""

from __future__ import annotations

from typing import Any, Callable

# `x-archive-agent`: the envelope agent is an Archive object whose Entity has
# one of these kinds.
AGENT_KINDS: dict[tuple[str, str], frozenset[str]] = {
    ("archive.claim/lead_sheet", "1"): frozenset({"person", "organisation"}),
    ("archive.claim/sensitivity", "1"): frozenset({"person"}),
    ("archive.claim/sharing_consent", "1"): frozenset({"person"}),
    ("archive.claim/span_annotation", "1"): frozenset({"person"}),
    ("archive.claim/work_origin", "1"): frozenset({"person"}),
}

# `x-archive-target-form`: (`by`, {role: `same` or `without`}). A claim pinned
# in a `without` role pins nothing in `by`; one pinned in a `same` role pins
# something in `by` exactly when this claim does.
TARGET_FORMS: dict[tuple[str, str], tuple[str, dict[str, str]]] = {
    ("archive.claim/meter_layers", "2"): ("source", {"corrects": "same", "meter": "without"}),
    ("archive.claim/melody", "1"): ("source", {"meter": "without"}),
}


class EnvelopeRuleError(ValueError):
    pass


def _roles(record: dict[str, Any], role: str) -> list[dict[str, Any]]:
    return [ref for ref in record.get("refs", []) or [] if isinstance(ref, dict) and ref.get("role") == role]


def check(record: dict[str, Any], contract: tuple[str, str], entity_kind: Callable[[str], str | None],
          records: dict[str, dict[str, Any]]) -> None:
    """Raise EnvelopeRuleError when `record` breaks its contract's agent or
    target-form rule. `entity_kind(object_id)` is the kind of an Entity World
    holds, or None; `records` maps revision IDs to the records World holds."""
    kinds = AGENT_KINDS.get(contract)
    if kinds is not None:
        agent = record.get("agent")
        kind = entity_kind(agent) if isinstance(agent, str) and agent.startswith("urn:uuid:") else None
        if kind not in kinds:
            raise EnvelopeRuleError(f"{contract[0]} version {contract[1]}: the agent must be an Archive object "
                                    f"identifying a {' or '.join(sorted(kinds))} Entity")
    form = TARGET_FORMS.get(contract)
    if form is not None:
        by, roles = form
        own = bool(_roles(record, by))
        for role, rule in sorted(roles.items()):
            for ref in _roles(record, role):
                target = records.get(ref.get("revision_id"))
                if target is None:
                    raise EnvelopeRuleError(f"{contract[0]} version {contract[1]}: {role} pins a revision "
                                            "World does not hold")
                pinned = bool(_roles(target, by))
                if (rule == "same" and pinned != own) or (rule == "without" and pinned):
                    raise EnvelopeRuleError(
                        f"{contract[0]} version {contract[1]}: {role} must pin a Claim "
                        + (f"with a {by} exactly when this one pins one" if rule == "same" else f"that pins no {by}"))
