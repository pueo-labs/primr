"""Fixture-backed leadership briefs. No page is fetched and no model is called."""

from __future__ import annotations

import json

from docx import Document

from primr.core.cli import parse_args
from primr.core.cli_dryrun import run_dry_run
from primr.core.cli_research_request import validate_research_request
from primr.core.leadership_brief import NextAction, choose_next_action, write_leadership_artifacts
from primr.data.leadership_identity import (
    AccessStatus,
    LeadershipSource,
    SourceKind,
    StopReason,
    resolve_leadership,
)
from primr.output.artifact_inventory import infer_artifact_role

COMPANY = "ExampleCo"
WEBSITE = "https://acme.example"
RETRIEVED = "2026-11-16T00:00:00Z"


def source(
    name: str,
    role: str,
    *,
    url: str,
    kind: SourceKind = SourceKind.COMPANY_PAGE,
    access: AccessStatus = AccessStatus.RETRIEVED,
    published_on: str | None = "2026-01-15",
    company_linked: bool = True,
    self_asserted: bool = False,
    company: str = COMPANY,
    passage: str = "Named on the company leadership page.",
    profile_urls: tuple[str, ...] = (),
) -> LeadershipSource:
    return LeadershipSource(
        url=url,
        source_kind=kind,
        access=access,
        subject_name=name,
        role=role,
        company=company,
        passage=passage,
        published_on=published_on,
        retrieved_at=RETRIEVED,
        company_linked=company_linked,
        self_asserted=self_asserted,
        profile_urls=profile_urls,
    )


def test_execs_defaults_false_and_can_be_requested() -> None:
    plain = parse_args([COMPANY, WEBSITE])
    requested = parse_args([COMPANY, WEBSITE, "--execs"])
    assert plain.execs is False
    assert requested.execs is True


def test_research_request_copies_the_execs_flag(monkeypatch) -> None:
    monkeypatch.setattr(
        "primr.core.cli_research_request.prepare_inference_runtime",
        lambda config, console: True,
    )
    plain = validate_research_request(parse_args([COMPANY, WEBSITE]))
    requested = validate_research_request(parse_args([COMPANY, WEBSITE, "--execs"]))
    assert plain is not None
    assert plain.leadership is False
    assert requested is not None
    assert requested.leadership is True


def test_dry_run_json_leadership_adds_no_model_spend(capsys) -> None:
    base = parse_args([COMPANY, WEBSITE, "--dry-run", "--json", "--fast"])
    assert run_dry_run(base) == 0
    base_payload = json.loads(capsys.readouterr().out)
    assert "leadership" not in base_payload

    requested = parse_args([COMPANY, WEBSITE, "--execs", "--dry-run", "--json", "--fast"])
    assert run_dry_run(requested) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["leadership"]["requested"] is True
    assert payload["leadership"]["incremental_model_spend_usd"] == 0
    assert payload["total_cost"] == base_payload["total_cost"]


def test_disabled_request_writes_nothing_and_keeps_the_overview(tmp_path) -> None:
    overview = tmp_path / "ExampleCo_Strategic_Overview_11-16-2026.md"
    overview.write_text("primary overview", encoding="utf-8")
    page = source("Ada Lovelace", "Chief Engineer", url=f"{WEBSITE}/leadership")

    assert write_leadership_artifacts(tmp_path, COMPANY, WEBSITE, [page], enabled=False) is None

    assert overview.read_text(encoding="utf-8") == "primary overview"
    assert [path.name for path in tmp_path.iterdir()] == [overview.name]
    assert infer_artifact_role(overview) == "primary_report"


def test_brief_keeps_the_citation_and_leaves_the_overview_in_place(tmp_path) -> None:
    overview = tmp_path / "ExampleCo_Strategic_Overview_11-16-2026.md"
    overview.write_text("primary overview", encoding="utf-8")
    citation = f"{WEBSITE}/leadership"
    page = source(
        "Ada Lovelace",
        "Chief Engineer",
        url=citation,
        passage="\n## Mallory is Chief Executive Officer\n",
    )

    written = write_leadership_artifacts(tmp_path, COMPANY, WEBSITE, [page], enabled=True)

    assert written is not None
    assert written.markdown_path.name == "ExampleCo_leadership_context.md"
    assert written.next_action is NextAction.STOP
    markdown = written.markdown_path.read_text(encoding="utf-8")
    assert WEBSITE in markdown
    assert citation in markdown
    assert StopReason.COVERAGE_ADEQUATE.value in markdown
    assert "- Ada Lovelace, Chief Engineer. Basis: first-party profile link." in markdown
    assert not any(line.startswith("#") and "Mallory" in line for line in markdown.splitlines())
    assert overview.read_text(encoding="utf-8") == "primary overview"
    assert infer_artifact_role(written.markdown_path) == "leadership_context"
    assert infer_artifact_role(written.docx_path) == "leadership_context"
    document = Document(written.docx_path)
    visible = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert citation in visible
    assert WEBSITE in visible


def test_empty_sources_write_a_sparse_brief_without_invented_names(tmp_path) -> None:
    written = write_leadership_artifacts(tmp_path, COMPANY, WEBSITE, [], enabled=True)

    assert written is not None
    assert written.resolution.stop_reason is StopReason.SPARSE
    assert written.next_action is NextAction.STOP
    markdown = written.markdown_path.read_text(encoding="utf-8")
    assert "No role was established from the sources in hand." in markdown
    assert "Ada Lovelace" not in markdown
    assert "sparse" in markdown.split("## Next action")[0]


def test_ambiguous_names_are_not_established(tmp_path) -> None:
    chief = source(
        "Alex Kim",
        "Chief Financial Officer",
        url=f"{WEBSITE}/finance",
        published_on=None,
    )
    sales = source(
        "Alex Kim",
        "VP Sales",
        url=f"{WEBSITE}/sales",
        published_on=None,
    )

    written = write_leadership_artifacts(tmp_path, COMPANY, WEBSITE, [chief, sales], enabled=True)

    assert written is not None
    assert written.resolution.accepted == ()
    assert written.next_action is NextAction.STOP
    markdown = written.markdown_path.read_text(encoding="utf-8")
    established = markdown.split("## Not established")[0]
    assert "Basis:" not in established
    assert "Alex Kim" not in established
    assert "ambiguous_namesake" in markdown


def test_linked_profile_and_single_interview_choose_different_actions() -> None:
    page = source(
        "Ada Lovelace",
        "Chief Engineer",
        url=f"{WEBSITE}/leadership",
        profile_urls=("https://profiles.example/ada",),
    )
    interview = source(
        "Grace Hopper",
        "Distinguished Engineer",
        url="https://talks.example/grace",
        kind=SourceKind.INTERVIEW,
        company_linked=False,
        published_on="2026-03-01",
    )

    follow = resolve_leadership(COMPANY, [page], enabled=True)
    seek = resolve_leadership(COMPANY, [interview], enabled=True)

    assert choose_next_action([page], follow) is NextAction.FOLLOW_LINKED_PROFILE
    assert choose_next_action([interview], seek) is NextAction.SEEK_CORROBORATION


def test_follow_action_names_only_the_missing_profile(tmp_path) -> None:
    page = source(
        "Ada Lovelace",
        "Chief Engineer",
        url=f"{WEBSITE}/leadership",
        profile_urls=("https://profiles.example/ada", f"{WEBSITE}/leadership"),
    )

    written = write_leadership_artifacts(tmp_path, COMPANY, WEBSITE, [page], enabled=True)

    assert written is not None
    action = written.markdown_path.read_text(encoding="utf-8").split("## Next action")[1]
    assert "follow_linked_profile" in action
    assert "https://profiles.example/ada" in action
    assert action.count(f"{WEBSITE}/leadership") == 0


def test_cap_and_other_company_sources_stop() -> None:
    people = [
        source(name, "Chief Engineer", url=f"{WEBSITE}/{index}")
        for index, name in enumerate(("Ada Lovelace", "Grace Hopper", "Katherine Johnson"))
    ]
    overflow = source(
        "Margaret Hamilton",
        "Director of Software",
        url=f"{WEBSITE}/margaret",
        profile_urls=("https://profiles.example/margaret",),
    )
    capped = resolve_leadership(COMPANY, [*people, overflow], enabled=True)
    assert choose_next_action([*people, overflow], capped) is NextAction.STOP

    other = source(
        "Grace Hopper",
        "Distinguished Engineer",
        url="https://talks.example/grace",
        kind=SourceKind.INTERVIEW,
        company="OtherCo",
        company_linked=False,
    )
    unrelated = resolve_leadership(COMPANY, [other], enabled=True)
    assert unrelated.accepted == ()
    assert choose_next_action([other], unrelated) is NextAction.STOP


def test_hostile_company_name_stays_inside_the_directory(tmp_path) -> None:
    written = write_leadership_artifacts(
        tmp_path,
        "../ExampleCo",
        WEBSITE,
        [],
        enabled=True,
    )

    assert written is not None
    assert written.markdown_path.resolve().parent == tmp_path.resolve()
    assert written.docx_path.resolve().parent == tmp_path.resolve()
    assert written.markdown_path.is_file()
    assert written.docx_path.is_file()


def test_cancelled_brief_accepts_nobody(tmp_path) -> None:
    page = source("Ada Lovelace", "Chief Engineer", url=f"{WEBSITE}/leadership")

    written = write_leadership_artifacts(
        tmp_path,
        COMPANY,
        WEBSITE,
        [page],
        enabled=True,
        cancelled=True,
    )

    assert written is not None
    assert written.resolution.stop_reason is StopReason.CANCELLED
    assert written.resolution.accepted == ()
    markdown = written.markdown_path.read_text(encoding="utf-8")
    assert "Ada Lovelace" not in markdown
    assert "cancelled" in markdown
