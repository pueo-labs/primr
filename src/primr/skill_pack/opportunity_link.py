"""Attach strategy opportunities to skill names already on a role plan.

The canonical strategy instruction remains the AI Strategy prompt. This module
does not read a report, call a model, or author a skill. A hiring-only plan
stays valid with an explicit empty link list.

Accepted links stay inside the saved-plan caps: 32 opportunities, 8 skill
names each, and text short enough to load again. A weak basis is stored as a
refusal. It is not turned into a skill.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from primr.skill_pack.schema import (
    ALLOWED_INTERVENTION_CLASSES,
    MAX_OPPORTUNITY_LINKS,
    MAX_OPPORTUNITY_NEEDS,
    MAX_OPPORTUNITY_REFUSALS,
    MAX_SKILLS_PER_OPPORTUNITY,
    OPPORTUNITY_TEXT_LIMITS,
    OpportunityLink,
    OpportunityRefusal,
    RolePlan,
    single_line,
)
from primr.skill_pack.validator import validate_kebab_case

_ACCEPTED_BASIS = "strategy_card"
_WEAK_REASONS = frozenset(
    {
        "hiring_ad",
        "recon_only",
        "sparse",
        "conflicting",
        "outdated",
        "inferred_role",
        "public_symptom",
        "adequate_solution",
    }
)


@dataclass(frozen=True)
class OpportunityNeed:
    """One structured opportunity a caller already decided, not free report text."""

    opportunity_id: str
    workflow: str
    intervention_class: str
    intervention_name: str
    skill_names: tuple[str, ...]
    basis: str
    evidence_note: str


@dataclass(frozen=True)
class OpportunityLinkage:
    """Accepted links and the candidates that stayed unlinked."""

    links: tuple[OpportunityLink, ...]
    refusals: tuple[OpportunityRefusal, ...]


def link_opportunities(
    skill_names: Sequence[str],
    needs: Sequence[OpportunityNeed],
) -> OpportunityLinkage:
    """Link only a strategy-card basis whose skill names are already in the roster."""

    if len(needs) > MAX_OPPORTUNITY_NEEDS:
        raise ValueError(f"at most {MAX_OPPORTUNITY_NEEDS} opportunities can be considered")
    roster = tuple(dict.fromkeys(single_line(name) for name in skill_names))
    accepted: dict[tuple[str, str], OpportunityLink] = {}
    order: list[tuple[str, str]] = []
    blocked: set[tuple[str, str]] = set()
    refusals: list[OpportunityRefusal] = []
    seen_refusals: set[tuple[str, str]] = set()

    for need in needs:
        cleaned = _clean_need(need)
        if not cleaned.opportunity_id:
            raise ValueError("opportunity_id must be non-empty")
        reason = _refusal_reason(cleaned)
        if reason is not None:
            _add_refusal(refusals, seen_refusals, cleaned.opportunity_id, reason)
            continue
        for skill_name in cleaned.skill_names:
            _link_skill(
                cleaned,
                skill_name,
                roster,
                accepted,
                order,
                blocked,
                refusals,
                seen_refusals,
            )

    links = tuple(accepted[key] for key in order if key not in blocked)
    if len(links) > MAX_OPPORTUNITY_LINKS:
        raise ValueError(f"at most {MAX_OPPORTUNITY_LINKS} opportunity links can be stored")
    if len(refusals) > MAX_OPPORTUNITY_REFUSALS:
        raise ValueError(f"at most {MAX_OPPORTUNITY_REFUSALS} opportunity refusals can be stored")
    return OpportunityLinkage(links=links, refusals=tuple(refusals))


def apply_opportunity_links(
    plan: RolePlan,
    skill_names: Sequence[str],
    needs: Sequence[OpportunityNeed],
) -> RolePlan:
    """Store the linkage on the plan. The role roster and approval basis stay put."""

    linkage = link_opportunities(skill_names, needs)
    plan.opportunity_links = list(linkage.links)
    plan.opportunity_refusals = list(linkage.refusals)
    return plan


def _clean_need(need: OpportunityNeed) -> OpportunityNeed:
    skill_names: list[str] = []
    for name in need.skill_names:
        cleaned_name = _bounded_line(name, "skill_name")
        if cleaned_name not in skill_names:
            skill_names.append(cleaned_name)
    return OpportunityNeed(
        opportunity_id=_bounded_line(need.opportunity_id, "opportunity_id"),
        workflow=_bounded_line(need.workflow, "workflow"),
        intervention_class=_bounded_line(need.intervention_class, "intervention_class"),
        intervention_name=_bounded_line(need.intervention_name, "intervention_name"),
        skill_names=tuple(skill_names),
        basis=need.basis,
        evidence_note=_bounded_line(need.evidence_note, "evidence_note"),
    )


def _bounded_line(text: str, field: str) -> str:
    limit = OPPORTUNITY_TEXT_LIMITS[field]
    cleaned = single_line(text)
    if len(cleaned) > limit:
        raise ValueError(f"{field} must contain at most {limit} characters")
    _reject_figures(cleaned)
    return cleaned


def _refusal_reason(need: OpportunityNeed) -> str | None:
    if need.basis in _WEAK_REASONS:
        return need.basis
    if need.basis != _ACCEPTED_BASIS:
        return "unsupported_basis"
    if need.intervention_class not in ALLOWED_INTERVENTION_CLASSES:
        return "unsupported_intervention"
    if not need.opportunity_id or not need.workflow:
        return "incomplete_opportunity"
    if not need.intervention_name or not need.evidence_note:
        return "incomplete_opportunity"
    if len(need.skill_names) > MAX_SKILLS_PER_OPPORTUNITY:
        return "too_many_skills"
    if not need.skill_names:
        return "missing_skill"
    return None


def _link_skill(
    need: OpportunityNeed,
    skill_name: str,
    roster: tuple[str, ...],
    accepted: dict[tuple[str, str], OpportunityLink],
    order: list[tuple[str, str]],
    blocked: set[tuple[str, str]],
    refusals: list[OpportunityRefusal],
    seen_refusals: set[tuple[str, str]],
) -> None:
    if skill_name not in roster or not validate_kebab_case(skill_name):
        _add_refusal(refusals, seen_refusals, need.opportunity_id, "unknown_skill")
        return
    key = (need.opportunity_id, skill_name)
    if key in blocked:
        return
    candidate = OpportunityLink(
        opportunity_id=need.opportunity_id,
        skill_name=skill_name,
        workflow=need.workflow,
        intervention_class=need.intervention_class,
        intervention_name=need.intervention_name,
        evidence_note=need.evidence_note,
    )
    existing = accepted.get(key)
    if existing is None:
        accepted[key] = candidate
        order.append(key)
        return
    if existing != candidate:
        blocked.add(key)
        del accepted[key]
        _add_refusal(refusals, seen_refusals, need.opportunity_id, "conflicting_link")


def _add_refusal(
    refusals: list[OpportunityRefusal],
    seen: set[tuple[str, str]],
    opportunity_id: str,
    reason: str,
) -> None:
    key = (opportunity_id, reason)
    if key in seen:
        return
    seen.add(key)
    refusals.append(OpportunityRefusal(opportunity_id=opportunity_id, reason=reason))


def _reject_figures(text: str) -> None:
    lowered = text.casefold()
    if "$" in text or "%" in text or "percent" in lowered or "dollars" in lowered:
        raise ValueError("an opportunity link must not state a savings figure")
