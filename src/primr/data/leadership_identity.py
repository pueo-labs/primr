"""Identity contract for optional leadership context.

Callers pass source records they have already retrieved. This module decides
which public professional roles are established for one company. It does not
fetch pages, call a model, or add a command-line flag. A matching name is
not an identity. A caller renders the brief. This module does not.

An accepted role needs one recorded basis: a first-party company source, two
independent professional sources that agree, or an explicit operator
assertion. Self-claims, undated role conflicts, and unlinked namesakes stay
unresolved.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

# The first slice studies at most three current leaders.
DEFAULT_MAX_SUBJECTS = 3


class SourceKind(Enum):
    """Where a leadership statement was published."""

    COMPANY_PAGE = "company_page"
    FILING = "filing"
    SPEAKER_BIO = "speaker_bio"
    PROFESSIONAL_PROFILE = "professional_profile"
    INTERVIEW = "interview"
    AUTHORED = "authored"


class AccessStatus(Enum):
    """How retrieval ended. Failure is not evidence that a person is absent."""

    RETRIEVED = "retrieved"
    NOT_FOUND = "not_found"
    BLOCKED = "blocked"
    TIMED_OUT = "timed_out"


class AssociationBasis(Enum):
    """Why a role was accepted. There is no numeric confidence score."""

    FIRST_PARTY_LINK = "first_party_profile_link"
    CORROBORATED_BIOGRAPHY = "corroborated_professional_biography"
    OPERATOR_ASSERTION = "operator_assertion"


class StopReason(Enum):
    """Why resolution stopped. Partial acceptance is not a full roster."""

    NOT_REQUESTED = "not_requested"
    CANCELLED = "cancelled"
    COVERAGE_ADEQUATE = "coverage_adequate"
    AMBIGUOUS_CANDIDATES = "ambiguous_candidates"
    ACCESS_RESTRICTED = "access_restricted"
    CAP_REACHED = "cap_reached"
    SPARSE = "sparse"


_INDEPENDENT_KINDS = frozenset({SourceKind.INTERVIEW, SourceKind.AUTHORED, SourceKind.FILING})
_ACCESS_FAILURES = frozenset({AccessStatus.BLOCKED, AccessStatus.TIMED_OUT})


@dataclass(frozen=True)
class LeadershipSource:
    """One retrieved statement about a person and a role."""

    url: str
    source_kind: SourceKind
    access: AccessStatus
    subject_name: str
    role: str
    company: str
    passage: str
    published_on: str | None
    retrieved_at: str
    company_linked: bool
    self_asserted: bool
    profile_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class OperatorAssertion:
    """An operator's explicit, recorded resolution. Scraped text cannot set this."""

    subject_name: str
    role: str
    note: str


@dataclass(frozen=True)
class LeadershipCandidate:
    """A role that was accepted or kept unresolved. Rejected people are not merged away."""

    name: str
    role: str
    sources: tuple[LeadershipSource, ...]
    accepted: bool
    basis: AssociationBasis | None
    rejection: str | None


@dataclass(frozen=True)
class LeadershipResolution:
    """The bounded result of one company leadership pass."""

    accepted: tuple[LeadershipCandidate, ...]
    rejected: tuple[LeadershipCandidate, ...]
    stop_reason: StopReason


def resolve_leadership(
    company: str,
    sources: Sequence[LeadershipSource],
    *,
    enabled: bool = False,
    max_subjects: int = DEFAULT_MAX_SUBJECTS,
    excluded_names: Sequence[str] = (),
    operator_assertions: Sequence[OperatorAssertion] = (),
    cancelled: bool = False,
) -> LeadershipResolution:
    """Resolve current roles for ``company``.

    ``enabled`` defaults false so a caller that forgets the opt-in does not
    turn source text into leadership findings. ``cancelled`` discards the
    pass before any role is accepted.
    """
    if not enabled:
        return LeadershipResolution((), (), StopReason.NOT_REQUESTED)
    if cancelled:
        return LeadershipResolution((), (), StopReason.CANCELLED)

    company_key = _norm(company)
    excluded = {_norm(name) for name in excluded_names if _norm(name)}
    considered = [source for source in sources if _norm(source.company) == company_key]
    accepted, rejected = _resolve_company_sources(considered)
    accepted, rejected = _attach_independent_sources(accepted, rejected, considered)
    accepted, rejected = _apply_operator_assertions(accepted, rejected, operator_assertions)
    accepted, rejected = _apply_exclusions(accepted, rejected, excluded)
    accepted, rejected, capped = _apply_cap(accepted, rejected, max_subjects)
    reason = _stop_reason(accepted, rejected, considered, capped)
    return LeadershipResolution(tuple(accepted), tuple(rejected), reason)


def _resolve_company_sources(
    sources: Sequence[LeadershipSource],
) -> tuple[list[LeadershipCandidate], list[LeadershipCandidate]]:
    grouped: dict[str, list[LeadershipSource]] = {}
    order: list[str] = []
    for source in sources:
        if not _is_first_party(source):
            continue
        key = _norm(source.subject_name)
        if key not in grouped:
            order.append(key)
            grouped[key] = []
        grouped[key].append(source)

    accepted: list[LeadershipCandidate] = []
    rejected: list[LeadershipCandidate] = []
    for key in order:
        group = grouped[key]
        chosen, stale = _current_role(group)
        if chosen is None:
            rejected.append(
                _candidate(group, accepted=False, basis=None, rejection="ambiguous_namesake")
            )
            continue
        accepted.append(
            _candidate(
                chosen, accepted=True, basis=AssociationBasis.FIRST_PARTY_LINK, rejection=None
            )
        )
        if stale:
            rejected.append(
                _candidate(stale, accepted=False, basis=None, rejection="stale_appointment")
            )
    return accepted, rejected


def _current_role(
    sources: Sequence[LeadershipSource],
) -> tuple[list[LeadershipSource] | None, list[LeadershipSource]]:
    by_role: dict[str, list[LeadershipSource]] = {}
    for source in sources:
        by_role.setdefault(_norm(source.role), []).append(source)
    if len(by_role) == 1:
        return list(sources), []

    dated = [
        (source, published) for source in sources if (published := _date_key(source.published_on))
    ]
    if len(dated) != len(sources):
        return None, []
    newest = max(published for _, published in dated)
    winning_roles = {_norm(source.role) for source, published in dated if published == newest}
    if len(winning_roles) != 1:
        return None, []
    role = next(iter(winning_roles))
    chosen = [source for source in sources if _norm(source.role) == role]
    stale = [source for source in sources if _norm(source.role) != role]
    return chosen, stale


def _attach_independent_sources(
    accepted: list[LeadershipCandidate],
    rejected: list[LeadershipCandidate],
    sources: Sequence[LeadershipSource],
) -> tuple[list[LeadershipCandidate], list[LeadershipCandidate]]:
    attached: set[int] = set()
    updated: list[LeadershipCandidate] = []
    for candidate in accepted:
        extra: list[LeadershipSource] = []
        linked = {url for source in candidate.sources for url in source.profile_urls}
        for index, source in enumerate(sources):
            if index in attached or _is_first_party(source):
                continue
            if not _same_person(candidate, source):
                continue
            if source.url in linked or _is_independent(source):
                extra.append(source)
                attached.add(index)
        updated.append(_with_sources(candidate, extra))
    accepted = updated

    leftovers: dict[tuple[str, str], list[LeadershipSource]] = {}
    for index, source in enumerate(sources):
        if index in attached or source.access is not AccessStatus.RETRIEVED:
            continue
        if _is_first_party(source) or source.self_asserted or not _named(source):
            if source.self_asserted and source.access is AccessStatus.RETRIEVED and _named(source):
                rejected.append(
                    _candidate((source,), accepted=False, basis=None, rejection="self_claim_only")
                )
            continue
        key = (_norm(source.subject_name), _norm(source.role))
        leftovers.setdefault(key, []).append(source)

    for group in leftovers.values():
        urls = {source.url for source in group}
        if len(group) >= 2 and len(urls) >= 2:
            accepted.append(
                _candidate(
                    group,
                    accepted=True,
                    basis=AssociationBasis.CORROBORATED_BIOGRAPHY,
                    rejection=None,
                )
            )
        else:
            rejected.append(
                _candidate(group, accepted=False, basis=None, rejection="unlinked_profile")
            )
    return accepted, rejected


def _apply_operator_assertions(
    accepted: list[LeadershipCandidate],
    rejected: list[LeadershipCandidate],
    assertions: Sequence[OperatorAssertion],
) -> tuple[list[LeadershipCandidate], list[LeadershipCandidate]]:
    for assertion in assertions:
        name = _norm(assertion.subject_name)
        role = _norm(assertion.role)
        if not name or not role:
            continue
        if any(_norm(item.name) == name and _norm(item.role) == role for item in accepted):
            continue
        accepted.append(
            LeadershipCandidate(
                name=assertion.subject_name.strip(),
                role=assertion.role.strip(),
                sources=(),
                accepted=True,
                basis=AssociationBasis.OPERATOR_ASSERTION,
                rejection=None,
            )
        )
    return accepted, rejected


def _apply_exclusions(
    accepted: list[LeadershipCandidate],
    rejected: list[LeadershipCandidate],
    excluded: set[str],
) -> tuple[list[LeadershipCandidate], list[LeadershipCandidate]]:
    if not excluded:
        return accepted, rejected
    kept: list[LeadershipCandidate] = []
    for candidate in accepted:
        if _norm(candidate.name) in excluded:
            rejected.append(
                LeadershipCandidate(
                    name=candidate.name,
                    role=candidate.role,
                    sources=candidate.sources,
                    accepted=False,
                    basis=None,
                    rejection="operator_excluded",
                )
            )
        else:
            kept.append(candidate)
    return kept, rejected


def _apply_cap(
    accepted: list[LeadershipCandidate],
    rejected: list[LeadershipCandidate],
    max_subjects: int,
) -> tuple[list[LeadershipCandidate], list[LeadershipCandidate], bool]:
    limit = max(0, max_subjects)
    if len(accepted) <= limit:
        return accepted, rejected, False
    overflow = accepted[limit:]
    rejected.extend(
        LeadershipCandidate(
            name=item.name,
            role=item.role,
            sources=item.sources,
            accepted=False,
            basis=None,
            rejection="cap_reached",
        )
        for item in overflow
    )
    return accepted[:limit], rejected, True


def _stop_reason(
    accepted: Sequence[LeadershipCandidate],
    rejected: Sequence[LeadershipCandidate],
    sources: Sequence[LeadershipSource],
    capped: bool,
) -> StopReason:
    if capped:
        return StopReason.CAP_REACHED
    if accepted:
        return StopReason.COVERAGE_ADEQUATE
    if any(item.rejection == "ambiguous_namesake" for item in rejected):
        return StopReason.AMBIGUOUS_CANDIDATES
    if sources and all(source.access in _ACCESS_FAILURES for source in sources):
        return StopReason.ACCESS_RESTRICTED
    if any(source.access in _ACCESS_FAILURES for source in sources) and not any(
        source.access is AccessStatus.RETRIEVED for source in sources
    ):
        return StopReason.ACCESS_RESTRICTED
    return StopReason.SPARSE


def _is_first_party(source: LeadershipSource) -> bool:
    return (
        source.company_linked
        and source.access is AccessStatus.RETRIEVED
        and _named(source)
        and not source.self_asserted
    )


def _is_independent(source: LeadershipSource) -> bool:
    return (
        source.access is AccessStatus.RETRIEVED
        and not source.self_asserted
        and source.source_kind in _INDEPENDENT_KINDS
    )


def _named(source: LeadershipSource) -> bool:
    return bool(_norm(source.subject_name) and _norm(source.role))


def _same_person(candidate: LeadershipCandidate, source: LeadershipSource) -> bool:
    return _norm(candidate.name) == _norm(source.subject_name) and _norm(candidate.role) == _norm(
        source.role
    )


def _candidate(
    sources: Sequence[LeadershipSource],
    *,
    accepted: bool,
    basis: AssociationBasis | None,
    rejection: str | None,
) -> LeadershipCandidate:
    first = sources[0]
    return LeadershipCandidate(
        name=first.subject_name.strip(),
        role=first.role.strip(),
        sources=tuple(sources),
        accepted=accepted,
        basis=basis,
        rejection=rejection,
    )


def _with_sources(
    candidate: LeadershipCandidate, extra: Sequence[LeadershipSource]
) -> LeadershipCandidate:
    if not extra:
        return candidate
    return LeadershipCandidate(
        name=candidate.name,
        role=candidate.role,
        sources=candidate.sources + tuple(extra),
        accepted=candidate.accepted,
        basis=candidate.basis,
        rejection=candidate.rejection,
    )


def _norm(value: str) -> str:
    return " ".join(value.casefold().replace(".", " ").split())


def _date_key(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-" and text[5:7].isdigit():
        year, month, day = text[:4], text[5:7], text[8:10]
        if year.isdigit() and day.isdigit() and 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return text
    return None
