"""Oracle for the synthetic AI Strategy intervention contract.

The canonical instruction is ``strategies/ai_strategy.yaml``. This module
does not classify a live company, call a model, or write a product artifact.
Tests use it to show that a different supported cause selects a different
intervention, and that a weak signal selects none.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

_OWNER = "Owner to confirm"
_BASELINE = "unknown"
_PERMISSIONS = "unknown"


class EvidencePosture(Enum):
    """How far the packet can go toward choosing an intervention."""

    SUPPORTED = "supported"
    PUBLIC_SYMPTOM_ONLY = "public_symptom_only"
    SPARSE = "sparse"
    CONFLICTING = "conflicting"
    OUTDATED = "outdated"
    HIRING_AD_ONLY = "hiring_ad_only"
    RECON_ONLY = "recon_only"
    INFERRED_ROLE_ONLY = "inferred_role_only"
    ADEQUATE_SOLUTION = "adequate_solution"


class InterventionClass(Enum):
    """The smallest useful intervention, or the choice to wait."""

    REUSABLE_SKILL = "reusable_skill"
    DETERMINISTIC_WORKFLOW = "deterministic_workflow"
    BOUNDED_AGENT = "bounded_agent"
    HUMAN_LED_DEFER_OR_NO_AI = "human_led_defer_or_no_ai"


CLASS_LABELS = {
    InterventionClass.REUSABLE_SKILL: "reusable skill",
    InterventionClass.DETERMINISTIC_WORKFLOW: "deterministic workflow or ordinary software",
    InterventionClass.BOUNDED_AGENT: "bounded agent",
    InterventionClass.HUMAN_LED_DEFER_OR_NO_AI: "human-led change, defer, or no AI",
}


@dataclass(frozen=True)
class Hypothesis:
    """One cause, and the intervention that follows only if that cause is supported."""

    hypothesis_id: str
    cause: str
    intervention_class: InterventionClass
    intervention_name: str
    workflow_change: str
    why_this: str
    why_not_alternatives: str
    direction: str
    validation: str
    allowed_actions: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        agent = self.intervention_class is InterventionClass.BOUNDED_AGENT
        if agent and not (self.allowed_actions and self.stop_conditions):
            raise ValueError("a bounded agent needs allowed actions and stop conditions")
        if not agent and (self.allowed_actions or self.stop_conditions):
            raise ValueError("only a bounded agent carries allowed actions or stop conditions")


@dataclass(frozen=True)
class EvidencePacket:
    """One synthetic company signal and the causes a reader might attach to it."""

    company: str
    website: str
    signal: str
    observed_on: str
    workflow: str
    posture: EvidencePosture
    hypotheses: tuple[Hypothesis, ...]
    current_solution: str
    validation: str

    def __post_init__(self) -> None:
        if not self.company.strip() or not self.workflow.strip():
            raise ValueError("company and workflow are required")
        if not self.hypotheses:
            raise ValueError("a packet needs at least one hypothesis")
        ids = [item.hypothesis_id for item in self.hypotheses]
        if len(ids) != len(set(ids)):
            raise ValueError("hypothesis ids must be unique")


@dataclass(frozen=True)
class InterventionDecision:
    """The intervention the packet supports, with unknowns left unknown."""

    company: str
    workflow: str
    intervention_class: InterventionClass
    intervention_name: str
    workflow_change: str
    selected_hypothesis_id: str | None
    rationale: str
    why_not_alternatives: str
    direction: str
    validation: str
    allowed_actions: tuple[str, ...]
    stop_conditions: tuple[str, ...]
    owner: str
    baseline: str
    permissions: str
    competing: tuple[str, ...]


@dataclass(frozen=True)
class _PostureReading:
    rationale: str
    why_not_alternatives: str
    direction: str
    workflow_change: str


_POSTURE_READINGS = {
    EvidencePosture.PUBLIC_SYMPTOM_ONLY: _PostureReading(
        rationale="The company reported a symptom. The report does not identify the cause.",
        why_not_alternatives=(
            "A reusable skill or a bounded agent answers a specific cause. "
            "Choose one only after a measurement separates the causes."
        ),
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.SPARSE: _PostureReading(
        rationale="The evidence is too sparse to select an intervention.",
        why_not_alternatives="A skill or an agent designed from a fragment would invent the cause.",
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.CONFLICTING: _PostureReading(
        rationale="The sources name different causes, so none is selected.",
        why_not_alternatives="Picking either competing cause would hide the conflict.",
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.OUTDATED: _PostureReading(
        rationale="The only signal is outdated, so it cannot select an intervention.",
        why_not_alternatives="An old complaint does not establish the current workflow.",
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.HIRING_AD_ONLY: _PostureReading(
        rationale="A job advertisement does not prove understaffing or a failed process.",
        why_not_alternatives="Hiring copy is not an operating problem to automate.",
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.RECON_ONLY: _PostureReading(
        rationale="DNS and recon do not prove licensed capacity or operational maturity.",
        why_not_alternatives="A hostname does not show how the work is done.",
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.INFERRED_ROLE_ONLY: _PostureReading(
        rationale="An inferred role is not an observed operating problem.",
        why_not_alternatives="A plausible job title does not identify a workflow to change.",
        direction="No benefit is claimed.",
        workflow_change="No workflow change is selected.",
    ),
    EvidencePosture.ADEQUATE_SOLUTION: _PostureReading(
        rationale="The current solution already meets the stated outcome. Keep it.",
        why_not_alternatives=(
            "A new skill or agent is not justified while that outcome still holds."
        ),
        direction="The stated outcome already holds, so no new gain is claimed.",
        workflow_change="Keep the current workflow.",
    ),
}


def distinguish_intervention(
    packet: EvidencePacket,
    *,
    hypothesis_id: str | None = None,
) -> InterventionDecision:
    """Select an intervention only when the packet's posture supports that cause.

    ``hypothesis_id`` is an explicit test choice. The signal text is not parsed,
    so a keyword such as missing inputs cannot by itself become a skill.
    """

    if packet.posture is EvidencePosture.SUPPORTED and hypothesis_id is not None:
        return _selected(packet, hypothesis_id)
    return _unselected(packet, measuring=packet.posture is EvidencePosture.SUPPORTED)


def render_distinction_markdown(packet: EvidencePacket, decision: InterventionDecision) -> str:
    """Render one packet and one decision as a paired reading."""

    lines = [
        f"# {packet.company} intervention distinction",
        "",
        "This reading applies the AI Strategy intervention contract to one synthetic packet.",
        "It is not the Strategic Overview, and it states no savings figure.",
        "",
        "## Problem and evidence",
        "",
        f"Company: {packet.company}",
        f"Website: {packet.website}",
        f"Workflow: {packet.workflow}",
        f"Signal: {packet.signal}",
        f"Observed: {packet.observed_on}",
        f"Evidence posture: {packet.posture.value}",
        "",
        "## Competing hypotheses",
        "",
    ]
    if decision.competing:
        lines.extend(f"- {item}" for item in decision.competing)
    else:
        lines.append("None remain beside the selected cause.")
    lines.extend(
        [
            "",
            "## Proposed intervention",
            "",
            f"Class: {CLASS_LABELS[decision.intervention_class]}",
            f"Name: {decision.intervention_name}",
            f"Workflow change: {decision.workflow_change}",
            f"Selected cause: {decision.selected_hypothesis_id or 'none'}",
            f"Owner: {decision.owner}",
            f"Baseline: {decision.baseline}",
            f"Permissions: {decision.permissions}",
            "",
            "## Why this instead of the alternative",
            "",
            decision.rationale,
            "",
            decision.why_not_alternatives,
            "",
            "## Expected direction of benefit",
            "",
            decision.direction,
            "",
            "## What to test next",
            "",
            decision.validation,
        ]
    )
    if decision.intervention_class is InterventionClass.BOUNDED_AGENT:
        lines.extend(["", "Allowed actions:", ""])
        lines.extend(f"- {action}" for action in decision.allowed_actions)
        lines.extend(["", "Stop conditions:", ""])
        lines.extend(f"- {condition}" for condition in decision.stop_conditions)
    elif decision.allowed_actions or decision.stop_conditions:
        raise ValueError("allowed actions belong only on a bounded agent")
    text = "\n".join(lines) + "\n"
    _reject_figures(text)
    return text


def render_hypothesis_pair(
    packet: EvidencePacket,
    left_hypothesis_id: str,
    right_hypothesis_id: str,
) -> str:
    """Render two supported causes for the same packet so the intervention change is visible."""

    if packet.posture is not EvidencePosture.SUPPORTED:
        raise ValueError("a hypothesis pair requires a supported packet")
    left = distinguish_intervention(packet, hypothesis_id=left_hypothesis_id)
    right = distinguish_intervention(packet, hypothesis_id=right_hypothesis_id)
    same_choice = (
        left.intervention_class is right.intervention_class
        and left.intervention_name == right.intervention_name
    )
    if same_choice:
        raise ValueError("the pair must show two different interventions")
    intro = (
        f"# {packet.company}: same public symptom, two supported causes\n\n"
        "The company, workflow, and signal stay fixed. "
        "The supported cause changes the intervention.\n\n"
    )
    body = (
        render_distinction_markdown(packet, left)
        + "\n"
        + render_distinction_markdown(packet, right)
    )
    return intro + body


def retitle(packet: EvidencePacket, *, company: str, website: str) -> EvidencePacket:
    """Change only the company name and website. The workflow stays the packet's own."""

    return replace(packet, company=company, website=website)


def with_posture(
    packet: EvidencePacket,
    posture: EvidencePosture,
    *,
    signal: str | None = None,
    observed_on: str | None = None,
) -> EvidencePacket:
    """Return the same causes under a weaker or different evidence posture."""

    return replace(
        packet,
        posture=posture,
        signal=packet.signal if signal is None else signal,
        observed_on=packet.observed_on if observed_on is None else observed_on,
    )


def _selected(packet: EvidencePacket, hypothesis_id: str) -> InterventionDecision:
    match = next(
        (item for item in packet.hypotheses if item.hypothesis_id == hypothesis_id),
        None,
    )
    if match is None:
        raise ValueError(f"unknown hypothesis: {hypothesis_id}")
    return _decision(
        packet,
        intervention_class=match.intervention_class,
        intervention_name=match.intervention_name,
        workflow_change=match.workflow_change,
        selected_hypothesis_id=match.hypothesis_id,
        rationale=match.why_this,
        why_not_alternatives=match.why_not_alternatives,
        direction=match.direction,
        validation=match.validation,
        allowed_actions=match.allowed_actions,
        stop_conditions=match.stop_conditions,
        competing=tuple(
            _hypothesis_line(item)
            for item in packet.hypotheses
            if item.hypothesis_id != hypothesis_id
        ),
    )


def _unselected(packet: EvidencePacket, *, measuring: bool) -> InterventionDecision:
    if measuring:
        reading = _PostureReading(
            rationale=(
                "Several causes fit this symptom. Measure the workflow before "
                "choosing an intervention."
            ),
            why_not_alternatives=(
                "Selecting a skill or an agent before the measurement would treat "
                "one hypothesis as a fact."
            ),
            direction="No benefit is claimed.",
            workflow_change="No workflow change is selected.",
        )
        name = "Defer selection"
    else:
        reading = _POSTURE_READINGS[packet.posture]
        name = (
            packet.current_solution
            if packet.posture is EvidencePosture.ADEQUATE_SOLUTION
            else "Defer selection"
        )
    return _decision(
        packet,
        intervention_class=InterventionClass.HUMAN_LED_DEFER_OR_NO_AI,
        intervention_name=name,
        workflow_change=reading.workflow_change,
        selected_hypothesis_id=None,
        rationale=reading.rationale,
        why_not_alternatives=reading.why_not_alternatives,
        direction=reading.direction,
        validation=packet.validation,
        allowed_actions=(),
        stop_conditions=(),
        competing=tuple(_hypothesis_line(item) for item in packet.hypotheses),
    )


def _decision(
    packet: EvidencePacket,
    *,
    intervention_class: InterventionClass,
    intervention_name: str,
    workflow_change: str,
    selected_hypothesis_id: str | None,
    rationale: str,
    why_not_alternatives: str,
    direction: str,
    validation: str,
    allowed_actions: tuple[str, ...],
    stop_conditions: tuple[str, ...],
    competing: tuple[str, ...],
) -> InterventionDecision:
    return InterventionDecision(
        company=packet.company,
        workflow=packet.workflow,
        intervention_class=intervention_class,
        intervention_name=intervention_name,
        workflow_change=workflow_change,
        selected_hypothesis_id=selected_hypothesis_id,
        rationale=rationale,
        why_not_alternatives=why_not_alternatives,
        direction=direction,
        validation=validation,
        allowed_actions=allowed_actions,
        stop_conditions=stop_conditions,
        owner=_OWNER,
        baseline=_BASELINE,
        permissions=_PERMISSIONS,
        competing=competing,
    )


def _hypothesis_line(item: Hypothesis) -> str:
    return f"{item.hypothesis_id}: {item.cause}"


def _reject_figures(text: str) -> None:
    lowered = text.casefold()
    if "$" in text or "%" in text or "percent" in lowered or "dollars" in lowered:
        raise ValueError("the distinction must not state a savings figure")


EXAMPLECO_QUOTES = EvidencePacket(
    company="ExampleCo",
    website="https://exampleco.example",
    signal=(
        "ExampleCo's 2026 customer note says quote turnaround has slowed. "
        "The supported fixture can tie that symptom to incomplete intake, "
        "document retrieval, a published rate card, or an approval rule."
    ),
    observed_on="2026-03-01",
    workflow="commercial quote preparation",
    posture=EvidencePosture.SUPPORTED,
    current_solution="Existing quote checklist",
    validation=(
        "Measure quote stage time and the share of quote requests that arrive "
        "missing required inputs."
    ),
    hypotheses=(
        Hypothesis(
            hypothesis_id="missing_inputs",
            cause="Quote requests come back because required inputs are missing.",
            intervention_class=InterventionClass.REUSABLE_SKILL,
            intervention_name="Quote intake completeness check",
            workflow_change="Apply a quote intake checklist before pricing begins.",
            why_this="Missing inputs are a recurring check inside the existing quoting tool.",
            why_not_alternatives="Document gathering and an approval change answer different causes.",
            direction="Fewer quote requests should come back for missing inputs.",
            validation="Count incomplete quote requests before piloting the intake check.",
        ),
        Hypothesis(
            hypothesis_id="retrieval_bottleneck",
            cause="Complete quote requests still wait on attachments that must be assembled.",
            intervention_class=InterventionClass.BOUNDED_AGENT,
            intervention_name="Quote document gathering",
            workflow_change="Assemble the quote file set the operator already holds for the request.",
            why_this="The wait is retrieval and assembly, which depends on which file is missing.",
            why_not_alternatives=(
                "An intake checklist does not fetch a missing attachment, "
                "and an approval rule is a later gate."
            ),
            direction="Quote files should be assembled with fewer manual searches.",
            validation=(
                "Time the retrieval stage on complete quote requests before allowing a bounded pass."
            ),
            allowed_actions=(
                "Read quote files the operator has already supplied for this request.",
                "List the quote files that are still missing.",
            ),
            stop_conditions=(
                "Stop when every required quote file is listed or marked missing.",
                "Stop before a second search, a send, or any file the operator did not supply.",
            ),
        ),
        Hypothesis(
            hypothesis_id="published_rate_card",
            cause="The quote price follows a published rate card with known steps.",
            intervention_class=InterventionClass.DETERMINISTIC_WORKFLOW,
            intervention_name="Published rate-card calculation",
            workflow_change="Calculate the quote from the published rate card in ordinary software.",
            why_this="The price steps are already known, so autonomy adds little.",
            why_not_alternatives=(
                "A skill or an agent does not add a decision the rate card does not already make."
            ),
            direction="Quote prices should follow the published rate card with fewer manual lookups.",
            validation="Compare calculated quote prices with the rate card on a sample of requests.",
        ),
        Hypothesis(
            hypothesis_id="approval_policy",
            cause="Completed quotes wait on a person because the approval rule requires it.",
            intervention_class=InterventionClass.HUMAN_LED_DEFER_OR_NO_AI,
            intervention_name="Quote approval policy change",
            workflow_change="Change the quote approval rule, or keep the human gate.",
            why_this="The constraint is the approval rule. A tool cannot grant the approval.",
            why_not_alternatives=(
                "The intake check and the document gathering pass address different causes."
            ),
            direction="Quote cycle time changes only if the approval rule changes.",
            validation="Record how long completed quotes wait at the approval gate.",
        ),
    ),
)

ACME_DISPATCH = EvidencePacket(
    company="Acme Corp",
    website="https://acme.example",
    signal=(
        "Acme Corp's 2026 service note says repeat truck rolls have risen. "
        "The supported fixture can tie that symptom to missing site notes, "
        "parts lookup, published mileage rules, or a same-day return policy."
    ),
    observed_on="2026-03-04",
    workflow="field service dispatch",
    posture=EvidencePosture.SUPPORTED,
    current_solution="Existing dispatch checklist",
    validation=(
        "Measure repeat-visit stage time and the share of visits that arrive missing site notes."
    ),
    hypotheses=(
        Hypothesis(
            hypothesis_id="missing_site_notes",
            cause="Technicians arrive without the site notes the visit needs.",
            intervention_class=InterventionClass.REUSABLE_SKILL,
            intervention_name="Pre-visit site-note check",
            workflow_change="Apply a site-note checklist before the technician is dispatched.",
            why_this="Missing site notes are a recurring check in the existing dispatch tool.",
            why_not_alternatives="Parts lookup and a same-day policy change answer different causes.",
            direction="Fewer visits should be dispatched without the site notes the technician needs.",
            validation="Count visits missing site notes before piloting the site-note check.",
        ),
        Hypothesis(
            hypothesis_id="parts_lookup",
            cause="The visit is ready, then waits while parts are looked up across systems.",
            intervention_class=InterventionClass.BOUNDED_AGENT,
            intervention_name="Service-parts lookup",
            workflow_change="Look up the parts records the operator has already opened for this visit.",
            why_this="The wait is iterative lookup across records, not a single missing field.",
            why_not_alternatives=(
                "A site-note checklist does not search parts records, "
                "and a return policy is a later gate."
            ),
            direction="Parts for a visit should be identified with fewer manual searches.",
            validation=(
                "Time the parts-lookup stage on otherwise ready visits before allowing a bounded pass."
            ),
            allowed_actions=(
                "Read parts records the operator has already opened for this visit.",
                "List the parts records that are still unresolved.",
            ),
            stop_conditions=(
                "Stop when every required parts record is listed or marked unresolved.",
                "Stop before a second search, an order, or any record the operator did not open.",
            ),
        ),
        Hypothesis(
            hypothesis_id="mileage_rules",
            cause="Dispatch eligibility follows a published mileage and zone table.",
            intervention_class=InterventionClass.DETERMINISTIC_WORKFLOW,
            intervention_name="Mileage and zone rules",
            workflow_change="Apply the published mileage table in ordinary dispatch software.",
            why_this="The eligibility steps are already known, so autonomy adds little.",
            why_not_alternatives=(
                "A skill or an agent does not add a decision the mileage table does not already make."
            ),
            direction="Dispatch eligibility should follow the published mileage table.",
            validation="Compare dispatch decisions with the mileage table on a sample of visits.",
        ),
        Hypothesis(
            hypothesis_id="same_day_policy",
            cause="A second visit on the same day is refused by policy, even when the crew is free.",
            intervention_class=InterventionClass.HUMAN_LED_DEFER_OR_NO_AI,
            intervention_name="Same-day return policy change",
            workflow_change="Change the same-day return rule, or keep the human gate.",
            why_this="The constraint is the return policy. A tool cannot waive it.",
            why_not_alternatives="The site-note check and the parts lookup address different causes.",
            direction="Repeat visits change only if the same-day return rule changes.",
            validation="Record how often a ready crew is refused a same-day return.",
        ),
    ),
)
