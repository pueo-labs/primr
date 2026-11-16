"""Tests for A2A task store adapter."""

from unittest.mock import MagicMock

import pytest

a2a = pytest.importorskip("a2a")

from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import GetTaskRequest, ListTasksRequest, TaskState
from a2a.utils.errors import TaskNotFoundError

from primr.a2a.agent_card import build_agent_card
from primr.a2a.call_context import TRUSTED_LOCAL_A2A_USER
from primr.a2a.task_store import PrimrTaskStore, _job_to_task_state
from primr.a2a.types import A2ATaskMapping
from primr.mcp_server.job_store import SingleJobStore
from primr.mcp_server.types import ResearchStage


class TestStageToStateMapping:
    """Tests for Primr stage → A2A state mapping."""

    def test_idle_maps_to_submitted(self):
        job = _make_job(stage=ResearchStage.IDLE)
        assert _job_to_task_state(job) == TaskState.TASK_STATE_SUBMITTED

    def test_accepted_maps_to_submitted(self):
        job = _make_job(stage=ResearchStage.ACCEPTED)
        assert _job_to_task_state(job) == TaskState.TASK_STATE_SUBMITTED

    def test_scraping_maps_to_working(self):
        job = _make_job(stage=ResearchStage.SCRAPING)
        assert _job_to_task_state(job) == TaskState.TASK_STATE_WORKING

    def test_completed_maps_to_completed(self):
        job = _make_job(stage=ResearchStage.COMPLETED)
        assert _job_to_task_state(job) == TaskState.TASK_STATE_COMPLETED

    def test_failed_maps_to_failed(self):
        job = _make_job(stage=ResearchStage.FAILED)
        assert _job_to_task_state(job) == TaskState.TASK_STATE_FAILED

    def test_cancelled_maps_to_canceled(self):
        job = _make_job(stage=ResearchStage.CANCELLED)
        assert _job_to_task_state(job) == TaskState.TASK_STATE_CANCELED


class TestPrimrTaskStore:
    """Tests for PrimrTaskStore."""

    @pytest.fixture
    def store(self, tmp_path):
        journal_path = str(tmp_path / "journal.json")
        job_store = SingleJobStore(journal_path=journal_path)
        return PrimrTaskStore(job_store), job_store

    def test_register_and_get_mapping(self, store):
        task_store, _ = store
        mapping = A2ATaskMapping(task_id="t-1", job_id="j-1", skill_id="check")
        task_store.register_mapping(mapping)
        result = task_store.get_mapping("t-1")
        assert result is not None
        assert result.job_id == "j-1"

    def test_get_mapping_missing(self, store):
        task_store, _ = store
        assert task_store.get_mapping("nonexistent") is None

    def test_get_job_id(self, store):
        task_store, _ = store
        mapping = A2ATaskMapping(task_id="t-1", job_id="j-1", skill_id="check")
        task_store.register_mapping(mapping)
        assert task_store.get_job_id("t-1") == "j-1"
        assert task_store.get_job_id("t-2") is None

    @pytest.mark.asyncio
    async def test_get_task_with_job(self, store):
        """Get task returns A2A task when job exists."""
        task_store, job_store = store
        job = job_store.create(company_name="Acme", mode="full", owner_client_id="test")
        mapping = A2ATaskMapping(task_id="t-1", job_id=job.job_id, skill_id="research")
        task_store.register_mapping(mapping)

        task = await task_store.get("t-1", _remote_context("test"))
        assert task is not None
        assert task.id == "t-1"
        assert task.status.state in (TaskState.TASK_STATE_SUBMITTED, TaskState.TASK_STATE_WORKING)

    @pytest.mark.asyncio
    async def test_get_task_missing_mapping(self, store):
        """Get task returns None for unknown task ID."""
        task_store, _ = store
        assert await task_store.get("unknown", ServerCallContext()) is None

    @pytest.mark.asyncio
    async def test_save_is_noop(self, store):
        """Save is a no-op since job store is source of truth."""
        task_store, job_store = store
        job = job_store.create(company_name="Acme", mode="full", owner_client_id="test")
        mapping = A2ATaskMapping(task_id="t-1", job_id=job.job_id, skill_id="research")
        task_store.register_mapping(mapping)

        task = await task_store.get("t-1", _remote_context("test"))
        assert task is not None
        await task_store.save(task, ServerCallContext())  # Should not raise

    @pytest.mark.asyncio
    async def test_delete_removes_mapping(self, store):
        task_store, _ = store
        mapping = A2ATaskMapping(task_id="t-1", job_id="j-1", skill_id="check")
        task_store.register_mapping(mapping)
        await task_store.delete("t-1", ServerCallContext())
        assert task_store.get_mapping("t-1") is None

    @pytest.mark.asyncio
    async def test_get_allows_remote_exact_owner(self, store):
        task_store, job_store = store
        job = job_store.create("Acme", "full", owner_client_id="client-1")
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-owned", job_id=job.job_id, skill_id="research")
        )

        task = await task_store.get("t-owned", _remote_context("client-1"))

        assert task is not None
        assert task.id == "t-owned"

    @pytest.mark.asyncio
    async def test_get_hides_cross_owner_like_unknown_task(self, store):
        task_store, job_store = store
        job = job_store.create("Acme", "full", owner_client_id="client-2")
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-private", job_id=job.job_id, skill_id="research")
        )
        context = _remote_context("client-1")

        assert await task_store.get("t-private", context) is None
        assert await task_store.get("t-unknown", context) is None

        handler = DefaultRequestHandler(
            agent_executor=MagicMock(),
            task_store=task_store,
            agent_card=build_agent_card(version="1.0.0"),
        )
        errors = []
        for task_id in ("t-private", "t-unknown"):
            with pytest.raises(TaskNotFoundError) as caught:
                await handler.on_get_task(GetTaskRequest(id=task_id), context)
            errors.append(caught.value.message)
        assert errors[0] == errors[1] == "Task not found"

    @pytest.mark.asyncio
    async def test_get_fails_closed_without_authenticated_context(self, store):
        task_store, job_store = store
        job = job_store.create("Acme", "full", owner_client_id="client-1")
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-private", job_id=job.job_id, skill_id="research")
        )

        assert await task_store.get("t-private", ServerCallContext()) is None

    @pytest.mark.asyncio
    async def test_get_rejects_authenticated_reserved_local_subject(self, store):
        task_store, job_store = store
        job = job_store.create("Acme", "full", owner_client_id="a2a")
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-local", job_id=job.job_id, skill_id="research")
        )

        assert await task_store.get("t-local", _remote_context("a2a")) is None
        assert await task_store.get("t-local", _local_context()) is not None

    @pytest.mark.asyncio
    async def test_admin_has_no_cross_owner_read_bypass(self, store):
        task_store, job_store = store
        job = job_store.create("Acme", "full", owner_client_id="client-1")
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-owned", job_id=job.job_id, skill_id="research")
        )
        admin_context = _remote_context("admin-client", scopes=["admin"])

        assert await task_store.get("t-owned", admin_context) is None

    @pytest.mark.asyncio
    async def test_list_returns_only_the_callers_job(self, store):
        task_store, job_store = store
        job = job_store.create("Acme", "full", owner_client_id="client-1")
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-owned", job_id=job.job_id, skill_id="research")
        )
        task_store.register_mapping(
            A2ATaskMapping(task_id="t-missing", job_id="missing-job", skill_id="research")
        )

        owned = await task_store.list(ListTasksRequest(), _remote_context("client-1"))
        hidden = await task_store.list(ListTasksRequest(), _remote_context("client-2"))

        assert [task.id for task in owned.tasks] == ["t-owned"]
        assert owned.total_size == 1
        assert list(hidden.tasks) == []


def test_unknown_stage_is_unspecified():
    job = _make_job()
    job.current_stage = type("Stage", (), {"value": "not-a-stage"})()
    assert _job_to_task_state(job) == TaskState.TASK_STATE_UNSPECIFIED


def _make_job(stage=ResearchStage.IDLE):
    """Create a minimal ResearchJobState for testing."""
    from datetime import datetime, timezone

    from primr.mcp_server.job_store import ResearchJobState

    job = ResearchJobState(
        job_id="test-job-001",
        company_name="Test",
        mode="full",
        start_time=datetime.now(timezone.utc),
        owner_client_id="test",
    )
    if stage != ResearchStage.IDLE:
        job.advance_stage(stage)
    return job


class _AuthenticatedUser(User):
    def __init__(self, user_name: str) -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def _local_context() -> ServerCallContext:
    return ServerCallContext(user=TRUSTED_LOCAL_A2A_USER)


def _remote_context(
    client_id: str,
    *,
    scopes: list[str] | None = None,
) -> ServerCallContext:
    return ServerCallContext(
        user=_AuthenticatedUser(client_id),
        state={"auth": MagicMock(scopes=scopes or [])},
    )
