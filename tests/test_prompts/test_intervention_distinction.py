"""The synthetic intervention oracle stays off the live strategy path."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from docx import Document

from primr.output.artifact_inventory import infer_artifact_role
from primr.output.markdown_converter import markdown_to_docx
from primr.prompts.intervention_distinction import (
    ACME_DISPATCH,
    CLASS_LABELS,
    EXAMPLECO_QUOTES,
    EvidencePacket,
    EvidencePosture,
    Hypothesis,
    InterventionClass,
    distinguish_intervention,
    render_distinction_markdown,
    render_hypothesis_pair,
    retitle,
    with_posture,
)

CONFIG_PATH = (
    Path(__file__).parents[2] / "src" / "primr" / "prompts" / "strategies" / "ai_strategy.yaml"
)
SRC_ROOT = Path(__file__).parents[2] / "src" / "primr"

_WEAK_POSTURES = (
    EvidencePosture.PUBLIC_SYMPTOM_ONLY,
    EvidencePosture.SPARSE,
    EvidencePosture.CONFLICTING,
    EvidencePosture.OUTDATED,
    EvidencePosture.HIRING_AD_ONLY,
    EvidencePosture.RECON_ONLY,
    EvidencePosture.INFERRED_ROLE_ONLY,
)


def test_oracle_labels_are_the_prompt_classes() -> None:
    contract = " ".join(
        yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))["hard_requirements"].lower().split()
    )

    for label in CLASS_LABELS.values():
        assert label.casefold() in contract


def test_supported_cause_changes_the_intervention() -> None:
    skill = distinguish_intervention(EXAMPLECO_QUOTES, hypothesis_id="missing_inputs")
    agent = distinguish_intervention(EXAMPLECO_QUOTES, hypothesis_id="retrieval_bottleneck")
    workflow = distinguish_intervention(EXAMPLECO_QUOTES, hypothesis_id="published_rate_card")
    human = distinguish_intervention(EXAMPLECO_QUOTES, hypothesis_id="approval_policy")

    assert skill.intervention_class is InterventionClass.REUSABLE_SKILL
    assert skill.intervention_name == "Quote intake completeness check"
    assert skill.allowed_actions == ()
    assert skill.stop_conditions == ()
    assert agent.intervention_class is InterventionClass.BOUNDED_AGENT
    assert agent.allowed_actions
    assert agent.stop_conditions
    assert workflow.intervention_class is InterventionClass.DETERMINISTIC_WORKFLOW
    assert human.intervention_class is InterventionClass.HUMAN_LED_DEFER_OR_NO_AI
    assert (
        len(
            {
                skill.intervention_name,
                agent.intervention_name,
                workflow.intervention_name,
                human.intervention_name,
            }
        )
        == 4
    )
    assert skill.owner == "Owner to confirm"
    assert skill.baseline == "unknown"
    assert skill.permissions == "unknown"
    assert skill.selected_hypothesis_id == "missing_inputs"
    assert "retrieval_bottleneck" in " ".join(skill.competing)


def test_unmeasured_symptom_does_not_pick_a_skill() -> None:
    decision = distinguish_intervention(EXAMPLECO_QUOTES, hypothesis_id=None)

    assert decision.intervention_class is InterventionClass.HUMAN_LED_DEFER_OR_NO_AI
    assert decision.intervention_name == "Defer selection"
    assert decision.selected_hypothesis_id is None
    assert decision.allowed_actions == ()
    assert "Measure the workflow" in decision.rationale
    assert len(decision.competing) == 4


@pytest.mark.parametrize("posture", _WEAK_POSTURES)
def test_weak_evidence_keeps_the_skill_hypothesis_unselected(posture: EvidencePosture) -> None:
    packet = with_posture(EXAMPLECO_QUOTES, posture)
    forced = distinguish_intervention(packet, hypothesis_id="missing_inputs")

    assert forced.intervention_class is InterventionClass.HUMAN_LED_DEFER_OR_NO_AI
    assert forced.intervention_name == "Defer selection"
    assert forced.selected_hypothesis_id is None
    assert forced.allowed_actions == ()
    assert "Quote intake completeness check" not in forced.intervention_name
    assert forced.baseline == "unknown"
    assert forced.permissions == "unknown"


def test_each_weak_posture_states_its_own_limit() -> None:
    expectations = {
        EvidencePosture.PUBLIC_SYMPTOM_ONLY: "does not identify the cause",
        EvidencePosture.SPARSE: "too sparse",
        EvidencePosture.CONFLICTING: "different causes",
        EvidencePosture.OUTDATED: "outdated",
        EvidencePosture.HIRING_AD_ONLY: "job advertisement does not prove",
        EvidencePosture.RECON_ONLY: "do not prove licensed capacity",
        EvidencePosture.INFERRED_ROLE_ONLY: "not an observed operating problem",
    }

    for posture, phrase in expectations.items():
        decision = distinguish_intervention(with_posture(EXAMPLECO_QUOTES, posture))
        assert phrase in decision.rationale


def test_adequate_solution_keeps_the_current_workflow() -> None:
    packet = with_posture(
        EXAMPLECO_QUOTES,
        EvidencePosture.ADEQUATE_SOLUTION,
        signal="ExampleCo says the existing quote checklist already meets the published turnaround.",
    )
    decision = distinguish_intervention(packet, hypothesis_id="missing_inputs")

    assert decision.intervention_name == "Existing quote checklist"
    assert decision.workflow_change == "Keep the current workflow."
    assert "already meets the stated outcome" in decision.rationale
    assert decision.direction.startswith("The stated outcome already holds")
    assert decision.allowed_actions == ()


def test_company_rename_is_not_a_different_workflow() -> None:
    renamed = retitle(EXAMPLECO_QUOTES, company="Acme Corp", website="https://acme.example")
    renamed_reading = render_distinction_markdown(
        renamed,
        distinguish_intervention(renamed, hypothesis_id="missing_inputs"),
    )
    acme_reading = render_distinction_markdown(
        ACME_DISPATCH,
        distinguish_intervention(ACME_DISPATCH, hypothesis_id="missing_site_notes"),
    )

    assert "Acme Corp" in renamed_reading
    assert "commercial quote preparation" in renamed_reading
    assert "Quote intake completeness check" in renamed_reading
    assert "field service dispatch" not in renamed_reading
    assert "field service dispatch" in acme_reading
    assert "Pre-visit site-note check" in acme_reading
    assert "quote" not in acme_reading.lower()
    assert "dispatch" not in renamed_reading.lower()


def test_hypothesis_pair_shows_both_interventions(tmp_path: Path) -> None:
    paired = render_hypothesis_pair(EXAMPLECO_QUOTES, "missing_inputs", "retrieval_bottleneck")
    path = tmp_path / "exampleco_intervention_distinction.md"
    path.write_text(paired, encoding="utf-8")
    docx_path = tmp_path / "exampleco_intervention_distinction.docx"
    markdown_to_docx(paired, docx_path)

    assert "Quote intake completeness check" in paired
    assert "Quote document gathering" in paired
    assert "Allowed actions:" in paired
    assert paired.count("Allowed actions:") == 1
    assert "same public symptom, two supported causes" in paired
    assert "$" not in paired
    assert "%" not in paired
    assert infer_artifact_role(path) == "report"
    visible = "\n".join(paragraph.text for paragraph in Document(docx_path).paragraphs)
    assert "Quote intake completeness check" in visible
    assert "Quote document gathering" in visible
    assert "Allowed actions:" in visible


def test_acme_cause_change_uses_dispatch_work_rather_than_quoting() -> None:
    skill = distinguish_intervention(ACME_DISPATCH, hypothesis_id="missing_site_notes")
    agent = distinguish_intervention(ACME_DISPATCH, hypothesis_id="parts_lookup")
    human = distinguish_intervention(ACME_DISPATCH, hypothesis_id="same_day_policy")
    reading = render_distinction_markdown(ACME_DISPATCH, agent)

    assert skill.workflow == "field service dispatch"
    assert agent.intervention_class is InterventionClass.BOUNDED_AGENT
    assert human.intervention_name == "Same-day return policy change"
    assert "quote" not in reading.lower()
    assert "Stop before a second search" in reading
    assert agent.owner == "Owner to confirm"


def test_unknown_hypothesis_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown hypothesis"):
        distinguish_intervention(EXAMPLECO_QUOTES, hypothesis_id="understaffing")


def test_packet_rejects_a_skill_that_carries_actions() -> None:
    skill = EXAMPLECO_QUOTES.hypotheses[0]
    with pytest.raises(ValueError, match="only a bounded agent"):
        Hypothesis(
            hypothesis_id=skill.hypothesis_id,
            cause=skill.cause,
            intervention_class=InterventionClass.REUSABLE_SKILL,
            intervention_name=skill.intervention_name,
            workflow_change=skill.workflow_change,
            why_this=skill.why_this,
            why_not_alternatives=skill.why_not_alternatives,
            direction=skill.direction,
            validation=skill.validation,
            allowed_actions=("Send the quote.",),
        )


def test_packet_rejects_duplicate_causes() -> None:
    first = EXAMPLECO_QUOTES.hypotheses[0]
    with pytest.raises(ValueError, match="unique"):
        EvidencePacket(
            company=EXAMPLECO_QUOTES.company,
            website=EXAMPLECO_QUOTES.website,
            signal=EXAMPLECO_QUOTES.signal,
            observed_on=EXAMPLECO_QUOTES.observed_on,
            workflow=EXAMPLECO_QUOTES.workflow,
            posture=EXAMPLECO_QUOTES.posture,
            hypotheses=(first, first),
            current_solution=EXAMPLECO_QUOTES.current_solution,
            validation=EXAMPLECO_QUOTES.validation,
        )


def test_live_pipeline_does_not_import_the_oracle() -> None:
    offenders = [
        path.relative_to(SRC_ROOT).as_posix()
        for path in SRC_ROOT.rglob("*.py")
        if path.name != "intervention_distinction.py"
        and "intervention_distinction" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_prompt_does_not_embed_the_synthetic_packet() -> None:
    text = CONFIG_PATH.read_text(encoding="utf-8")

    assert "Quote intake completeness check" not in text
    assert "exampleco.example" not in text
    assert "Pre-visit site-note check" not in text
