"""
Tests for state reconciliation.

Tests:
- Timeout detection
- Manifest-based status update
- Cancellation timeout handling

Requirements: 12.2, 12.3, 12.4
"""

from __future__ import annotations

import tempfile
from datetime import datetime, timedelta

import pytest

from deploy.control_plane.job_store import (
    CostEstimate,
    InMemoryJobStore,
    JobInputs,
    JobRecord,
    JobStatus,
    JobTiming,
    format_timestamp,
    utc_now,
)
from deploy.control_plane.reconciler import (
    Reconciler,
    ReconciliationConfig,
    ReconciliationResult,
)
from deploy.manifest import ArtifactMeta, JobCost, JobManifest, JobVersions
from deploy.storage import LocalStore


def create_test_job(
    job_id: str,
    status: JobStatus,
    started_at: datetime | None = None,
    cancel_requested_at: datetime | None = None,
) -> JobRecord:
    """Create a test job record."""
    now = utc_now()
    return JobRecord(
        job_id=job_id,
        deployment="test",
        idempotency_key=f"key-{job_id}",
        api_key_hash="test_key",
        canonical_hash=f"hash-{job_id}",
        status=status,
        inputs=JobInputs(company_name="Test", company_url="https://test.com", mode="scrape"),
        expected_artifacts=["test.txt"],
        estimate=CostEstimate(cost_usd=0.1, duration_minutes=5),
        timing=JobTiming(
            submitted_at=format_timestamp(now - timedelta(hours=1)),
            started_at=format_timestamp(started_at)
            if started_at
            else format_timestamp(now - timedelta(minutes=30)),
            cancel_requested_at=format_timestamp(cancel_requested_at)
            if cancel_requested_at
            else None,
        ),
    )


def create_test_manifest(
    job_id: str,
    status: str,
    completed_at: datetime | None = None,
    error: str | None = None,
) -> JobManifest:
    """Create a test manifest."""
    from deploy.manifest import JobInputs, JobTiming

    now = utc_now()
    return JobManifest(
        job_id=job_id,
        idempotency_key=f"key-{job_id}",
        deployment="test",
        execution_id="exec-123",
        attempt=1,
        status=status,
        inputs=JobInputs(
            company_name="Test Co",
            company_url="https://test.com",
            mode="scrape",
        ),
        expected_artifacts=["test.txt"],
        timing=JobTiming(
            submitted_at=format_timestamp(now - timedelta(hours=1)),
            started_at=format_timestamp(now - timedelta(minutes=30)),
            completed_at=format_timestamp(completed_at) if completed_at else format_timestamp(now),
        ),
        cost=JobCost(estimated_usd=0.1),
        artifacts={
            "test.txt": ArtifactMeta(
                size_bytes=100,
                checksum_sha256="abc123",
            ),
        },
        versions=JobVersions(primr="1.0.0", runner="1.0.0"),
        error=error,
    )


class TestReconciler:
    """Tests for the Reconciler class."""

    @pytest.fixture
    def job_store(self) -> InMemoryJobStore:
        """Create a test job store."""
        return InMemoryJobStore()

    @pytest.fixture
    def artifact_store(self) -> LocalStore:
        """Create a test artifact store."""
        temp_dir = tempfile.mkdtemp(prefix="test_artifacts_")
        return LocalStore(temp_dir, "test")

    @pytest.fixture
    def reconciler(self, job_store: InMemoryJobStore, artifact_store: LocalStore) -> Reconciler:
        """Create a test reconciler."""
        config = ReconciliationConfig(
            max_duration_seconds=3600,  # 1 hour
            cancellation_grace_seconds=300,  # 5 minutes
            heartbeat_stale_seconds=600,  # 10 minutes
        )
        return Reconciler(job_store, artifact_store, config)

    def test_reconcile_empty_store(self, reconciler: Reconciler) -> None:
        """Reconciliation with no jobs returns zero counts."""
        result = reconciler.reconcile()

        assert result.jobs_checked == 0
        assert result.timeout_reconciled == 0
        assert result.manifest_reconciled == 0
        assert result.cancellation_timeout == 0
        assert result.errors == 0

    def test_timeout_detection(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """Jobs running beyond max_duration are marked as timeout."""
        # Create a job that started 2 hours ago (beyond 1 hour limit)
        old_start = utc_now() - timedelta(hours=2)
        job = create_test_job("timeout-job", JobStatus.RUNNING, started_at=old_start)
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        assert result.jobs_checked == 1
        assert result.timeout_reconciled == 1

        # Verify job was updated
        updated = job_store.get("timeout-job")
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.error_message == "timeout_reconciled"
        assert updated.no_runner_manifest is True

    def test_manifest_reconciliation(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
        artifact_store: LocalStore,
    ) -> None:
        """Jobs with manifest are updated to manifest status."""
        # Create a running job
        job = create_test_job("manifest-job", JobStatus.RUNNING)
        job_store.put_if_not_exists(job)

        # Write a manifest indicating success
        manifest = create_test_manifest("manifest-job", "SUCCEEDED")
        artifact_store.put_manifest("manifest-job", manifest)

        result = reconciler.reconcile()

        assert result.jobs_checked == 1
        assert result.manifest_reconciled == 1

        # Verify job was updated
        updated = job_store.get("manifest-job")
        assert updated is not None
        assert updated.status == JobStatus.SUCCEEDED

    def test_cancellation_timeout(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """A cancel request older than the grace period stops the wait."""
        old_request = utc_now() - timedelta(hours=2)
        job = create_test_job(
            "cancel-job",
            JobStatus.CANCEL_REQUESTED,
            started_at=utc_now() - timedelta(minutes=10),
            cancel_requested_at=old_request,
        )
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        assert result.jobs_checked == 1
        assert result.cancellation_timeout == 1

        # Verify job was updated
        updated = job_store.get("cancel-job")
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.error_message == "cancellation_timeout"
        assert updated.no_runner_manifest is True
        assert updated.status != JobStatus.CANCELLED

    def test_early_cancel_keeps_its_own_grace_period(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """A cancel ten minutes into a run waits out the grace period, not the job limit."""
        now = utc_now()
        job = create_test_job(
            "early-cancel",
            JobStatus.CANCEL_REQUESTED,
            started_at=now - timedelta(minutes=10),
            cancel_requested_at=now - timedelta(minutes=1),
        )
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        assert result.cancellation_timeout == 0
        updated = job_store.get("early-cancel")
        assert updated is not None
        assert updated.status == JobStatus.CANCEL_REQUESTED

    def test_late_cancel_still_receives_grace_period(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """A job older than the run limit still gets the full grace period after cancel."""
        now = utc_now()
        job = create_test_job(
            "late-cancel",
            JobStatus.CANCEL_REQUESTED,
            started_at=now - timedelta(hours=3),
            cancel_requested_at=now - timedelta(seconds=30),
        )
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        assert result.cancellation_timeout == 0
        updated = job_store.get("late-cancel")
        assert updated is not None
        assert updated.status == JobStatus.CANCEL_REQUESTED

    def test_grace_deadline_is_exclusive(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """The wait ends only after the grace period, not on the deadline itself."""
        now = utc_now()
        before = create_test_job(
            "before-deadline",
            JobStatus.CANCEL_REQUESTED,
            cancel_requested_at=now - timedelta(seconds=290),
        )
        after = create_test_job(
            "after-deadline",
            JobStatus.CANCEL_REQUESTED,
            cancel_requested_at=now - timedelta(seconds=310),
        )
        job_store.put_if_not_exists(before)
        job_store.put_if_not_exists(after)

        result = reconciler.reconcile()

        assert result.cancellation_timeout == 1
        still_waiting = job_store.get("before-deadline")
        timed_out = job_store.get("after-deadline")
        assert still_waiting is not None
        assert timed_out is not None
        assert still_waiting.status == JobStatus.CANCEL_REQUESTED
        assert timed_out.status == JobStatus.FAILED

    def test_missing_started_at_still_times_out_from_cancel_request(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """A missing start time does not disable the cancel grace clock."""
        job = create_test_job(
            "no-start",
            JobStatus.CANCEL_REQUESTED,
            cancel_requested_at=utc_now() - timedelta(minutes=10),
        )
        job.timing.started_at = None
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        assert result.cancellation_timeout == 1
        updated = job_store.get("no-start")
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.error_message == "cancellation_timeout"

    def test_legacy_record_starts_grace_at_first_observation(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """A stored cancel with no request time is not failed on the first look."""
        job = create_test_job(
            "legacy-cancel",
            JobStatus.CANCEL_REQUESTED,
            started_at=utc_now() - timedelta(hours=3),
        )
        assert job.timing.cancel_requested_at is None
        job_store.put_if_not_exists(job)

        first = reconciler.reconcile()

        assert first.cancellation_timeout == 0
        observed = job_store.get("legacy-cancel")
        assert observed is not None
        assert observed.status == JobStatus.CANCEL_REQUESTED
        assert observed.timing.cancel_requested_at is not None

        observed.timing.cancel_requested_at = format_timestamp(utc_now() - timedelta(minutes=10))
        job_store.update(observed)
        second = reconciler.reconcile()

        assert second.cancellation_timeout == 1
        finished = job_store.get("legacy-cancel")
        assert finished is not None
        assert finished.status == JobStatus.FAILED

    def test_manifest_wins_over_an_expired_grace_period(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
        artifact_store: LocalStore,
    ) -> None:
        """A terminal manifest is applied even when the grace period has elapsed."""
        job = create_test_job(
            "manifest-wins",
            JobStatus.CANCEL_REQUESTED,
            cancel_requested_at=utc_now() - timedelta(minutes=10),
        )
        job_store.put_if_not_exists(job)
        artifact_store.put_manifest(
            "manifest-wins",
            create_test_manifest("manifest-wins", "CANCELLED"),
        )

        result = reconciler.reconcile()

        assert result.manifest_reconciled == 1
        assert result.cancellation_timeout == 0
        updated = job_store.get("manifest-wins")
        assert updated is not None
        assert updated.status == JobStatus.CANCELLED

    def test_legacy_timing_round_trip_omits_unknown_cancel_time(self) -> None:
        """Records written before the field existed still load."""
        timing = JobTiming.from_dict(
            {
                "submitted_at": "2026-01-01T00:00:00Z",
                "started_at": "2026-01-01T00:10:00Z",
                "completed_at": None,
            }
        )
        assert timing.cancel_requested_at is None
        assert JobTiming.from_dict(timing.to_dict()).cancel_requested_at is None
        timing.cancel_requested_at = "2026-01-01T00:15:00Z"
        restored = JobTiming.from_dict(timing.to_dict())
        assert restored.cancel_requested_at == "2026-01-01T00:15:00Z"

    def test_cancel_with_manifest(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
        artifact_store: LocalStore,
    ) -> None:
        """Jobs in CANCEL_REQUESTED with manifest are updated from manifest."""
        # Create a cancel-requested job
        job = create_test_job("cancel-manifest-job", JobStatus.CANCEL_REQUESTED)
        job_store.put_if_not_exists(job)

        # Write a CANCELLED manifest
        manifest = create_test_manifest("cancel-manifest-job", "CANCELLED")
        artifact_store.put_manifest("cancel-manifest-job", manifest)

        result = reconciler.reconcile()

        assert result.jobs_checked == 1
        assert result.manifest_reconciled == 1

        # Verify job was updated
        updated = job_store.get("cancel-manifest-job")
        assert updated is not None
        assert updated.status == JobStatus.CANCELLED

    def test_healthy_job_not_modified(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """Jobs within time limits are not modified."""
        # Create a job that started recently
        recent_start = utc_now() - timedelta(minutes=10)
        job = create_test_job("healthy-job", JobStatus.RUNNING, started_at=recent_start)
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        assert result.jobs_checked == 1
        assert result.timeout_reconciled == 0
        assert result.manifest_reconciled == 0

        # Verify job was not modified
        updated = job_store.get("healthy-job")
        assert updated is not None
        assert updated.status == JobStatus.RUNNING

    def test_queued_jobs_not_checked(
        self,
        reconciler: Reconciler,
        job_store: InMemoryJobStore,
    ) -> None:
        """Jobs in QUEUED state are not checked."""
        job = create_test_job("queued-job", JobStatus.QUEUED)
        job_store.put_if_not_exists(job)

        result = reconciler.reconcile()

        # QUEUED jobs are not in active_statuses
        assert result.jobs_checked == 0

    def test_result_to_dict(self) -> None:
        """ReconciliationResult can be serialized to dict."""
        result = ReconciliationResult(
            jobs_checked=10,
            timeout_reconciled=2,
            manifest_reconciled=3,
            cancellation_timeout=1,
            errors=0,
        )

        d = result.to_dict()
        assert d["jobs_checked"] == 10
        assert d["timeout_reconciled"] == 2
        assert d["manifest_reconciled"] == 3
        assert d["cancellation_timeout"] == 1
        assert d["errors"] == 0
