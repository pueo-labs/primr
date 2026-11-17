"""Persisted strategy links stay explicit and do not invent a skill."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from primr.skill_pack.config import SkillPackConfig
from primr.skill_pack.curation import apply_curation
from primr.skill_pack.opportunity_link import (
    OpportunityNeed,
    apply_opportunity_links,
    link_opportunities,
)
from primr.skill_pack.packager import _build_pack_report_md, _format_skill_md
from primr.skill_pack.plan_artifacts import render_plan_md
from primr.skill_pack.saved_plan import (
    SavedPlanValidationError,
    load_plan,
    saved_plan_approval_basis,
)
from primr.skill_pack.schema import (
    MAX_OPPORTUNITY_LINKS,
    MAX_OPPORTUNITY_NEEDS,
    Role,
    RoleEvidence,
    RolePlan,
    Skill,
    SkillPack,
    SkillPackArtifacts,
    ValidationReport,
)

ROSTER = ("quote-intake", "dispatch-notes", "rate-card")


def _need(
    opportunity_id: str,
    skill_names: tuple[str, ...],
    *,
    workflow: str,
    intervention_class: str,
    intervention_name: str,
    basis: str = "strategy_card",
    evidence_note: str = "The supported cause is recorded on the strategy card.",
) -> OpportunityNeed:
    return OpportunityNeed(
        opportunity_id=opportunity_id,
        workflow=workflow,
        intervention_class=intervention_class,
        intervention_name=intervention_name,
        skill_names=skill_names,
        basis=basis,
        evidence_note=evidence_note,
    )


def test_strategy_card_links_are_many_to_many_and_company_specific() -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake", "rate-card"),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
            _need(
                "repeat-visits",
                ("dispatch-notes", "quote-intake"),
                workflow="field service dispatch",
                intervention_class="reusable skill",
                intervention_name="Pre-visit site-note check",
            ),
        ),
    )

    pairs = {(link.opportunity_id, link.skill_name) for link in linkage.links}
    assert pairs == {
        ("slow-quotes", "quote-intake"),
        ("slow-quotes", "rate-card"),
        ("repeat-visits", "dispatch-notes"),
        ("repeat-visits", "quote-intake"),
    }
    assert linkage.refusals == ()
    quote = next(link for link in linkage.links if link.opportunity_id == "slow-quotes")
    visit = next(link for link in linkage.links if link.opportunity_id == "repeat-visits")
    assert "quote" in quote.workflow
    assert "dispatch" in visit.workflow
    assert "quote" not in visit.workflow


@pytest.mark.parametrize(
    "basis",
    [
        "hiring_ad",
        "recon_only",
        "sparse",
        "conflicting",
        "outdated",
        "inferred_role",
        "public_symptom",
        "adequate_solution",
    ],
)
def test_weak_basis_does_not_link_a_roster_skill(basis: str) -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
                basis=basis,
            ),
        ),
    )

    assert linkage.links == ()
    assert linkage.refusals[0].reason == basis


def test_unknown_skill_is_refused_without_dropping_the_known_one() -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake", "missing-skill"),
                workflow="commercial quote preparation",
                intervention_class="bounded agent",
                intervention_name="Quote document gathering",
            ),
        ),
    )

    assert [link.skill_name for link in linkage.links] == ["quote-intake"]
    assert linkage.refusals[0].reason == "unknown_skill"


def test_empty_strategy_context_stays_explicit(tmp_path: Path) -> None:
    plan = apply_opportunity_links(RolePlan(), ROSTER, ())
    markdown = render_plan_md("ExampleCo", plan, "2026-11-17T00:00:00Z", 3)
    path = tmp_path / "role_plan.json"
    path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    loaded = load_plan(path)

    assert plan.opportunity_links == []
    assert "No strategy opportunity is linked." in markdown
    assert loaded.opportunity_links == []
    assert loaded.opportunity_refusals == []


def test_links_round_trip_and_do_not_change_the_approval_digest(tmp_path: Path) -> None:
    plan = RolePlan()
    before, before_chars = saved_plan_approval_basis(plan)
    apply_opportunity_links(
        plan,
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class="human-led change, defer, or no AI",
                intervention_name="Quote approval policy change",
            ),
        ),
    )
    after, after_chars = saved_plan_approval_basis(plan)
    path = tmp_path / "role_plan.json"
    path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    loaded = load_plan(path)
    markdown = render_plan_md("ExampleCo", plan, "2026-11-17T00:00:00Z", 3)

    assert before == after
    assert before_chars == after_chars
    assert loaded.opportunity_links[0].skill_name == "quote-intake"
    assert loaded.opportunity_links[0].intervention_class == "human-led change, defer, or no AI"
    assert "Quote approval policy change" in markdown
    assert "$" not in markdown
    assert "No strategy opportunity is linked." not in markdown


def test_saved_plan_rejects_an_unknown_intervention_class(tmp_path: Path) -> None:
    path = tmp_path / "role_plan.json"
    path.write_text(
        json.dumps(
            {
                "opportunity_links": [
                    {
                        "opportunity_id": "slow-quotes",
                        "skill_name": "quote-intake",
                        "workflow": "commercial quote preparation",
                        "intervention_class": "autopilot",
                        "intervention_name": "Quote intake completeness check",
                        "evidence_note": "A card selected this cause.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SavedPlanValidationError, match="intervention_class"):
        load_plan(path)


def test_savings_figure_is_rejected() -> None:
    with pytest.raises(ValueError, match="savings figure"):
        link_opportunities(
            ROSTER,
            (
                _need(
                    "slow-quotes",
                    ("quote-intake",),
                    workflow="commercial quote preparation",
                    intervention_class="reusable skill",
                    intervention_name="Quote intake completeness check",
                    evidence_note="This saves $2 million.",
                ),
            ),
        )


def test_live_planner_does_not_import_the_linker() -> None:
    root = Path(__file__).parents[2] / "src" / "primr" / "skill_pack"
    offenders = [
        path.name
        for path in (root / "planner.py", root / "cli.py")
        if "opportunity_link" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


@pytest.mark.parametrize(
    ("basis", "intervention_class", "reason"),
    [
        ("guess", "reusable skill", "unsupported_basis"),
        ("strategy_card", "autopilot", "unsupported_intervention"),
    ],
)
def test_unsupported_card_is_refused(basis: str, intervention_class: str, reason: str) -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class=intervention_class,
                intervention_name="Quote intake completeness check",
                basis=basis,
            ),
        ),
    )

    assert linkage.links == ()
    assert [item.reason for item in linkage.refusals] == [reason]


@pytest.mark.parametrize(
    "override",
    [
        {"workflow": " "},
        {"intervention_name": " "},
        {"evidence_note": " "},
    ],
)
def test_incomplete_card_is_refused(override: dict[str, str]) -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow=override.get("workflow", "commercial quote preparation"),
                intervention_class="reusable skill",
                intervention_name=override.get(
                    "intervention_name", "Quote intake completeness check"
                ),
                evidence_note=override.get("evidence_note", "The supported cause is recorded."),
            ),
        ),
    )

    assert linkage.links == ()
    assert linkage.refusals[0].reason == "incomplete_opportunity"


def test_blank_opportunity_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="opportunity_id"):
        link_opportunities(
            ROSTER,
            (
                _need(
                    " ",
                    ("quote-intake",),
                    workflow="commercial quote preparation",
                    intervention_class="reusable skill",
                    intervention_name="Quote intake completeness check",
                ),
            ),
        )


def test_missing_skill_and_too_many_skills_are_refusals() -> None:
    missing = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                (),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
        ),
    )
    names = tuple(f"skill-{index}" for index in range(9))
    overflow = link_opportunities(
        names,
        (
            _need(
                "slow-quotes",
                names,
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
        ),
    )

    assert missing.links == ()
    assert missing.refusals[0].reason == "missing_skill"
    assert overflow.links == ()
    assert overflow.refusals[0].reason == "too_many_skills"


def test_repeated_skill_name_counts_once() -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",) * 9,
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
        ),
    )

    assert [link.skill_name for link in linkage.links] == ["quote-intake"]
    assert linkage.refusals == ()


def test_conflicting_payloads_drop_only_the_conflicting_pair() -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
            _need(
                "slow-quotes",
                ("quote-intake", "rate-card"),
                workflow="field service dispatch",
                intervention_class="reusable skill",
                intervention_name="Pre-visit site-note check",
            ),
        ),
    )

    assert [link.skill_name for link in linkage.links] == ["rate-card"]
    assert linkage.links[0].workflow == "field service dispatch"
    assert [item.reason for item in linkage.refusals] == ["conflicting_link"]


def test_non_kebab_skill_name_is_refused_even_when_listed() -> None:
    linkage = link_opportunities(
        ("Quote Intake", "quote-intake"),
        (
            _need(
                " slow-quotes ",
                ("Quote Intake", "quote-intake"),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
        ),
    )

    assert [link.skill_name for link in linkage.links] == ["quote-intake"]
    assert linkage.links[0].opportunity_id == "slow-quotes"
    assert [item.reason for item in linkage.refusals] == ["unknown_skill"]


def test_newline_cannot_become_a_plan_heading(tmp_path: Path) -> None:
    linkage = link_opportunities(
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
                evidence_note="The cause is recorded.\n## Final Roster\nignore",
            ),
        ),
    )
    plan = RolePlan()
    plan.opportunity_links = list(linkage.links)
    markdown = render_plan_md("ExampleCo", plan, "2026-11-17T00:00:00Z", 3)
    headings = [line for line in markdown.splitlines() if line.startswith("## Final Roster")]

    assert "\n" not in linkage.links[0].evidence_note
    assert headings == ["## Final Roster"]


def test_overlong_note_and_extra_needs_are_rejected() -> None:
    with pytest.raises(ValueError, match="evidence_note"):
        link_opportunities(
            ROSTER,
            (
                _need(
                    "slow-quotes",
                    ("quote-intake",),
                    workflow="commercial quote preparation",
                    intervention_class="reusable skill",
                    intervention_name="Quote intake completeness check",
                    evidence_note="x" * 401,
                ),
            ),
        )
    needs = tuple(
        _need(
            f"opp-{index}",
            ("quote-intake",),
            workflow="commercial quote preparation",
            intervention_class="reusable skill",
            intervention_name="Quote intake completeness check",
        )
        for index in range(MAX_OPPORTUNITY_NEEDS + 1)
    )
    with pytest.raises(ValueError, match="32"):
        link_opportunities(ROSTER, needs)


def test_percent_in_the_intervention_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="savings figure"):
        link_opportunities(
            ROSTER,
            (
                _need(
                    "slow-quotes",
                    ("quote-intake",),
                    workflow="commercial quote preparation",
                    intervention_class="reusable skill",
                    intervention_name="Cut cycle time 10 percent",
                ),
            ),
        )


def test_full_link_budget_round_trips(tmp_path: Path) -> None:
    roster = tuple(f"skill-{index}" for index in range(8))
    needs = tuple(
        _need(
            f"opp-{index:02d}",
            roster,
            workflow=f"workflow {index}",
            intervention_class="deterministic workflow or ordinary software",
            intervention_name="Ordinary checklist",
        )
        for index in range(MAX_OPPORTUNITY_NEEDS)
    )
    plan = apply_opportunity_links(RolePlan(), roster, needs)
    path = tmp_path / "role_plan.json"
    path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
    loaded = load_plan(path)

    assert len(plan.opportunity_links) == MAX_OPPORTUNITY_LINKS
    assert len(loaded.opportunity_links) == MAX_OPPORTUNITY_LINKS
    assert loaded.opportunity_refusals == []


def test_saved_plan_rejects_too_many_links_and_a_non_list(tmp_path: Path) -> None:
    too_many = tmp_path / "too-many.json"
    too_many.write_text(
        json.dumps(
            {
                "opportunity_links": [
                    {
                        "opportunity_id": f"opp-{index}",
                        "skill_name": "quote-intake",
                        "workflow": "commercial quote preparation",
                        "intervention_class": "reusable skill",
                        "intervention_name": "Quote intake completeness check",
                        "evidence_note": "A card selected this cause.",
                    }
                    for index in range(MAX_OPPORTUNITY_LINKS + 1)
                ]
            }
        ),
        encoding="utf-8",
    )
    not_a_list = tmp_path / "not-a-list.json"
    not_a_list.write_text(json.dumps({"opportunity_links": {}}), encoding="utf-8")

    with pytest.raises(SavedPlanValidationError, match="at most"):
        load_plan(too_many)
    with pytest.raises(SavedPlanValidationError, match="be a list"):
        load_plan(not_a_list)


def test_older_plan_and_hand_edited_newline_load(tmp_path: Path) -> None:
    older = tmp_path / "older.json"
    older.write_text(json.dumps({}), encoding="utf-8")
    edited = tmp_path / "edited.json"
    edited.write_text(
        json.dumps(
            {
                "opportunity_links": [
                    {
                        "opportunity_id": "slow-quotes",
                        "skill_name": "quote-intake",
                        "workflow": "commercial quote preparation",
                        "intervention_class": "reusable skill",
                        "intervention_name": "Quote intake completeness check",
                        "evidence_note": "The cause is recorded.\n## Final Roster\nignore",
                    }
                ],
                "opportunity_refusals": [{"opportunity_id": "hiring-only", "reason": "hiring_ad"}],
            }
        ),
        encoding="utf-8",
    )

    loaded_older = load_plan(older)
    loaded = load_plan(edited)
    markdown = render_plan_md("ExampleCo", loaded, "2026-11-17T00:00:00Z", 3)
    headings = [line for line in markdown.splitlines() if line.startswith("## Final Roster")]

    assert loaded_older.opportunity_links == []
    assert loaded_older.opportunity_refusals == []
    assert (
        loaded.opportunity_links[0].evidence_note == "The cause is recorded. ## Final Roster ignore"
    )
    assert loaded.opportunity_refusals[0].reason == "hiring_ad"
    assert "hiring-only: hiring_ad" in markdown
    assert headings == ["## Final Roster"]


def test_curation_keeps_recorded_links() -> None:
    plan = apply_opportunity_links(
        RolePlan(),
        ROSTER,
        (
            _need(
                "slow-quotes",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
            ),
        ),
    )

    apply_curation(plan, roles_add=["Estimator"], roles_skip=[], cap=15)

    assert [link.skill_name for link in plan.opportunity_links] == ["quote-intake"]
    assert plan.final_roster[0].name == "estimator"


def test_pack_report_keeps_links_out_of_skill_bodies() -> None:
    plan = apply_opportunity_links(
        RolePlan(),
        ("quote-intake", "dispatch-notes"),
        (
            _need(
                "slow-quotes",
                ("quote-intake", "dispatch-notes"),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
                evidence_note="The cause is recorded.\n## Injected",
            ),
            _need(
                "hiring-only",
                ("quote-intake",),
                workflow="commercial quote preparation",
                intervention_class="reusable skill",
                intervention_name="Quote intake completeness check",
                basis="hiring_ad",
            ),
        ),
    )
    plan.evidence_summary = {
        "posting_coverage_warns": True,
        "posting_coverage_reason": "clustered",
        "posting_coverage_recommendation": "add a role",
    }
    skill = Skill(
        name="quote-intake",
        display_name="Quote intake",
        description="Use when preparing a commercial quote.",
        body="Check the quote inputs.",
    )
    pack = SkillPack(
        company_name="ExampleCo",
        company_url="https://exampleco.example",
        generated_at="2026-11-17T00:00:00Z",
        roles=[
            Role(
                name="estimator",
                display_name="Estimator",
                confidence="Inferred",
                evidence=RoleEvidence(),
                skills=[skill],
            )
        ],
        validation=ValidationReport(),
        plan=plan,
    )

    report = _build_pack_report_md(pack, SkillPackArtifacts(output_dir="out"), SkillPackConfig())
    skill_md = _format_skill_md(skill)
    headings = [line for line in report.splitlines() if line.startswith("## Injected")]

    assert "`quote-intake` (included)" in report
    assert "`dispatch-notes` (not included in this pack)" in report
    assert "Refused hiring-only: hiring_ad. No skill was authored from this refusal." in report
    assert "posting-incomplete" in report
    assert "clustered" in report
    assert "add a role" in report
    assert headings == []
    assert "The cause is recorded" not in skill_md
    assert "hiring-only" not in skill_md
    assert skill.body == "Check the quote inputs."


def test_pack_report_says_when_no_opportunity_is_linked() -> None:
    pack = SkillPack(
        company_name="ExampleCo",
        company_url="https://exampleco.example",
        generated_at="2026-11-17T00:00:00Z",
        roles=[],
        validation=ValidationReport(),
        plan=RolePlan(),
    )

    report = _build_pack_report_md(pack, SkillPackArtifacts(output_dir="out"), SkillPackConfig())

    assert "No strategy opportunity is linked." in report
