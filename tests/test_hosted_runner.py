"""Hosted Ubuntu jobs stay on the current generally available image."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
HOSTED_UBUNTU = "ubuntu-26.04"

# These jobs run on GitHub-hosted Ubuntu. The floating latest label still
# selects 24.04 while that label moves, so the pin is the current GA image.
EXPECTED_UBUNTU_JOBS = {
    "ci.yml": {"lint-and-test", "a2a-tests", "supply-chain-scan", "container-builds"},
    "codeql.yml": {"analyze"},
    "dependency-audit.yml": {"python-advisories", "repository-supply-chain"},
    "docs.yml": {"build", "deploy"},
    "release.yml": {"build", "publish", "github-release"},
    "scorecard.yml": {"analysis"},
}


def test_hosted_ubuntu_jobs_pin_the_current_ga_image() -> None:
    found: dict[str, set[str]] = {}
    for path in sorted(WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        jobs = document["jobs"]
        assert isinstance(jobs, dict)
        for name, job in jobs.items():
            runs_on = job["runs-on"]
            assert isinstance(runs_on, str)
            assert "self-hosted" not in runs_on
            if runs_on.startswith("ubuntu-"):
                assert runs_on == HOSTED_UBUNTU
                found.setdefault(path.name, set()).add(name)
                continue
            assert "${{" in runs_on
            assert job["strategy"]["matrix"]["os"] == ["windows-latest", "macos-latest"]
            assert path.name == "ci.yml"
            assert name == "cross-platform-test"
    assert found == EXPECTED_UBUNTU_JOBS
