"""Render optional leadership context from sources the caller already holds.

The brief is a separate artifact. It does not search the web, call a model,
or replace the Strategic Overview. ``enabled=False`` writes nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from primr.data.leadership_identity import (
    DEFAULT_MAX_SUBJECTS,
    AccessStatus,
    AssociationBasis,
    LeadershipResolution,
    LeadershipSource,
    OperatorAssertion,
    SourceKind,
    StopReason,
    resolve_leadership,
)
from primr.output.markdown_converter import markdown_to_docx
from primr.utils.atomic_io import atomic_write_text
from primr.utils.validators import sanitize_for_filename

_INDEPENDENT_KINDS = frozenset({SourceKind.INTERVIEW, SourceKind.AUTHORED, SourceKind.FILING})
_HARD_STOPS = frozenset(
    {
        StopReason.NOT_REQUESTED,
        StopReason.CANCELLED,
        StopReason.AMBIGUOUS_CANDIDATES,
        StopReason.ACCESS_RESTRICTED,
        StopReason.CAP_REACHED,
    }
)
_BASIS_LABELS = {
    AssociationBasis.FIRST_PARTY_LINK: "first-party profile link",
    AssociationBasis.CORROBORATED_BIOGRAPHY: "corroborated professional biography",
    AssociationBasis.OPERATOR_ASSERTION: "operator assertion",
}
_REJECTION_LABELS = {
    "stale_appointment": "stale appointment",
    "ambiguous_namesake": "ambiguous namesake",
    "self_claim_only": "self-claim only",
    "unlinked_profile": "unlinked profile",
    "operator_excluded": "operator excluded",
    "cap_reached": "cap reached",
}


class NextAction(Enum):
    """One adaptive lookup, or a stop. This slice does not perform the lookup."""

    STOP = "stop"
    FOLLOW_LINKED_PROFILE = "follow_linked_profile"
    SEEK_CORROBORATION = "seek_corroboration"


_ACTION_TEXT = {
    NextAction.STOP: "Stop. The sources in hand do not justify another leadership lookup.",
    NextAction.FOLLOW_LINKED_PROFILE: (
        "Follow the company-linked profile URL that is not yet in the source set."
    ),
    NextAction.SEEK_CORROBORATION: (
        "Seek one more independent professional source before treating the role as established."
    ),
}


@dataclass(frozen=True)
class LeadershipBrief:
    """Paths and decisions for one rendered leadership context."""

    markdown_path: Path
    docx_path: Path
    resolution: LeadershipResolution
    next_action: NextAction


def choose_next_action(
    sources: Sequence[LeadershipSource],
    resolution: LeadershipResolution,
) -> NextAction:
    """Pick the next lookup from a finished resolution.

    Ambiguous, cancelled, capped, access-restricted, and not-requested passes
    stop. A company page that names a profile URL absent from ``sources`` asks
    for that page. Exactly one independent retrieved source, with no
    first-party acceptance, asks for corroboration.
    """
    if resolution.stop_reason in _HARD_STOPS:
        return NextAction.STOP
    if _unfetched_profile_urls(sources, resolution):
        return NextAction.FOLLOW_LINKED_PROFILE
    if _needs_corroboration(resolution):
        return NextAction.SEEK_CORROBORATION
    return NextAction.STOP


def render_leadership_markdown(
    company: str,
    website: str,
    resolution: LeadershipResolution,
    next_action: NextAction,
    *,
    sources: Sequence[LeadershipSource] = (),
) -> str:
    """Render one brief. Passage text is flattened so it cannot add headings."""
    safe_company = _one_line(company, limit=200) or "Company"
    safe_website = _public_url(website) or _one_line(website, limit=300)
    lines = [
        f"# Leadership context: {safe_company}",
        "",
        f"Website: {safe_website}",
        "",
        "This brief uses sources already in hand. It does not search the web",
        "and it does not call a model. It is not the Strategic Overview.",
        "",
    ]
    if not sources:
        lines.extend(
            [
                "This research run did not supply structured leadership sources.",
                "An empty source list is not a finding that the company has no leaders.",
                "",
            ]
        )
    lines.extend(
        [
            "## Stop reason",
            "",
            resolution.stop_reason.value,
            "",
            "A blocked, timed-out, or missing source is not evidence that a person is absent.",
            "A rejection records why a role was not established.",
            "",
            "## Established roles",
            "",
        ]
    )
    lines.extend(_established_lines(resolution))
    lines.extend(["", "## Not established", ""])
    lines.extend(_unresolved_lines(resolution))
    lines.extend(["", "## Next action", "", next_action.value, "", _ACTION_TEXT[next_action]])
    if next_action is NextAction.FOLLOW_LINKED_PROFILE:
        for url in _unfetched_profile_urls(sources, resolution):
            lines.append(f"- {url}")
    lines.append("")
    return "\n".join(lines)


def write_leadership_artifacts(
    directory: str | Path,
    company: str,
    website: str,
    sources: Sequence[LeadershipSource],
    *,
    enabled: bool,
    max_subjects: int = DEFAULT_MAX_SUBJECTS,
    excluded_names: Sequence[str] = (),
    operator_assertions: Sequence[OperatorAssertion] = (),
    cancelled: bool = False,
) -> LeadershipBrief | None:
    """Write Markdown and Word briefs, or nothing when the request is off."""
    if not enabled:
        return None
    resolution = resolve_leadership(
        company,
        sources,
        enabled=True,
        max_subjects=max_subjects,
        excluded_names=excluded_names,
        operator_assertions=operator_assertions,
        cancelled=cancelled,
    )
    next_action = choose_next_action(sources, resolution)
    markdown = render_leadership_markdown(
        company,
        website,
        resolution,
        next_action,
        sources=sources,
    )
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    stem = _artifact_stem(company)
    markdown_path = _contained_file(root, f"{stem}.md")
    docx_path = _contained_file(root, f"{stem}.docx")
    atomic_write_text(markdown_path, markdown)
    markdown_to_docx(markdown, docx_path)
    return LeadershipBrief(markdown_path, docx_path, resolution, next_action)


def _needs_corroboration(resolution: LeadershipResolution) -> bool:
    if any(item.basis is AssociationBasis.FIRST_PARTY_LINK for item in resolution.accepted):
        return False
    independent = [
        source
        for candidate in (*resolution.accepted, *resolution.rejected)
        for source in candidate.sources
        if _is_independent(source)
    ]
    return len(independent) == 1


def _unfetched_profile_urls(
    sources: Sequence[LeadershipSource],
    resolution: LeadershipResolution,
) -> tuple[str, ...]:
    known = {url for source in sources if (url := _public_url(source.url))}
    missing: list[str] = []
    seen: set[str] = set()
    for candidate in resolution.accepted:
        for source in candidate.sources:
            if not _is_company_page(source):
                continue
            for raw in source.profile_urls:
                url = _public_url(raw)
                if url is None or url in known or url in seen:
                    continue
                seen.add(url)
                missing.append(url)
    return tuple(missing)


def _established_lines(resolution: LeadershipResolution) -> list[str]:
    if not resolution.accepted:
        return ["No role was established from the sources in hand."]
    lines: list[str] = []
    for candidate in resolution.accepted:
        basis = (
            _BASIS_LABELS.get(candidate.basis, "recorded basis")
            if candidate.basis
            else "recorded basis"
        )
        name = _one_line(candidate.name, limit=200) or "Unnamed"
        role = _one_line(candidate.role, limit=200) or "Unstated role"
        lines.append(f"- {name}, {role}. Basis: {basis}.")
        for source in candidate.sources:
            url = _public_url(source.url)
            if url:
                lines.append(f"  - {url}")
            passage = _one_line(source.passage, limit=240)
            if passage:
                lines.append(f"  - Source text: {passage}")
    return lines


def _unresolved_lines(resolution: LeadershipResolution) -> list[str]:
    if not resolution.rejected:
        return ["No unresolved role was recorded."]
    lines: list[str] = []
    for candidate in resolution.rejected:
        reason = candidate.rejection or "unresolved"
        label = _REJECTION_LABELS.get(reason, "unresolved")
        name = _one_line(candidate.name, limit=200) or "Unnamed"
        role = _one_line(candidate.role, limit=200) or "Unstated role"
        lines.append(f"- {name}, {role}. Reason: {label} ({reason}).")
    return lines


def _is_company_page(source: LeadershipSource) -> bool:
    return (
        source.company_linked
        and source.access is AccessStatus.RETRIEVED
        and not source.self_asserted
        and bool(_one_line(source.subject_name, limit=200))
        and bool(_one_line(source.role, limit=200))
    )


def _is_independent(source: LeadershipSource) -> bool:
    return (
        source.access is AccessStatus.RETRIEVED
        and not source.self_asserted
        and source.source_kind in _INDEPENDENT_KINDS
    )


def _artifact_stem(company: str) -> str:
    token = sanitize_for_filename(_one_line(company, limit=200) or "company", max_length=80)
    return f"{token}_leadership_context"


def _contained_file(directory: Path, filename: str) -> Path:
    root = directory.resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise ValueError(f"leadership artifact name escapes the output directory: {filename}")
    return path


def _public_url(value: str) -> str | None:
    text = "".join(value.split())
    if text.casefold().startswith(("http://", "https://")):
        return text[:500]
    return None


def _one_line(value: str, *, limit: int) -> str:
    """Collapse a scraped field onto one line so it cannot open a new heading."""
    text = " ".join(value.split())
    if len(text) > limit:
        text = text[: limit - 3].rstrip() + "..."
    return text.strip()
