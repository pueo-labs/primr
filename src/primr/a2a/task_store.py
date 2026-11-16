"""A2A task store adapter wrapping Primr's SingleJobStore.

Maps A2A task IDs to Primr job IDs, enforcing the single-job model
across both MCP and A2A protocols.

Requires: pip install primr[a2a]
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING

from a2a.helpers import new_text_message
from a2a.server.tasks import TaskStore
from a2a.types.a2a_pb2 import (
    Artifact,
    ListTasksRequest,
    ListTasksResponse,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import (
    ListTasksCursor,
    decode_list_tasks_cursor,
    decode_page_token,
    encode_list_tasks_cursor,
)

from primr.a2a.call_context import context_client_id

if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext

    from primr.a2a.types import A2ATaskMapping
    from primr.mcp_server.job_store import ResearchJobState, SingleJobStore

logger = logging.getLogger(__name__)

# Map Primr stages to A2A task states
_STAGE_TO_STATE: dict[str, TaskState] = {
    "idle": TaskState.TASK_STATE_SUBMITTED,
    "accepted": TaskState.TASK_STATE_SUBMITTED,
    "scraping": TaskState.TASK_STATE_WORKING,
    "extracting": TaskState.TASK_STATE_WORKING,
    "deep_research": TaskState.TASK_STATE_WORKING,
    "writing": TaskState.TASK_STATE_WORKING,
    "qa": TaskState.TASK_STATE_WORKING,
    "completed": TaskState.TASK_STATE_COMPLETED,
    "failed": TaskState.TASK_STATE_FAILED,
    "cancelled": TaskState.TASK_STATE_CANCELED,
}


def _job_to_task_state(job: ResearchJobState) -> TaskState:
    """Map a Primr job stage to an A2A TaskState."""
    return _STAGE_TO_STATE.get(job.current_stage.value, TaskState.TASK_STATE_UNSPECIFIED)


def _agent_message(text: str, task_id: str, context_id: str, message_id: str):
    message = new_text_message(text, context_id=context_id, task_id=task_id)
    message.message_id = message_id
    return message


def _job_to_task(job: ResearchJobState, task_id: str, context_id: str) -> Task:
    """Convert a Primr job to an A2A Task object."""
    state = _job_to_task_state(job)

    message_text = f"Research job {job.job_id}: {job.current_stage.value}"
    if job.stage_progress_percent is not None:
        message_text += f" ({job.stage_progress_percent}%)"
    if job.error_message:
        message_text += f" - {job.error_message}"

    status = TaskStatus(
        state=state,
        message=_agent_message(
            message_text,
            task_id,
            context_id,
            f"status-{job.job_id}",
        ),
    )

    artifacts: list[Artifact] = []
    if state == TaskState.TASK_STATE_COMPLETED and job.output_paths:
        artifacts.append(
            Artifact(
                artifact_id=f"report-{job.job_id}",
                parts=[
                    Part(text=f"Report available at: {', '.join(job.output_paths)}"),
                ],
            )
        )

    return Task(
        id=task_id,
        context_id=context_id,
        status=status,
        artifacts=artifacts,
    )


def _list_sort_key(task: Task) -> tuple[bool, int, str]:
    """Match ``ListTasksCursor.sort_key`` so page tokens stay SDK-compatible."""
    has_timestamp = task.HasField("status") and task.status.HasField("timestamp")
    return (
        has_timestamp,
        task.status.timestamp.ToNanoseconds() if has_timestamp else 0,
        task.id,
    )


def _cursor_for(task: Task) -> ListTasksCursor:
    has_timestamp, timestamp_ns, task_id = _list_sort_key(task)
    return ListTasksCursor(
        timestamp_ns=timestamp_ns if has_timestamp else None,
        task_id=task_id,
    )


def _page_tasks(tasks: Sequence[Task], params: ListTasksRequest) -> ListTasksResponse:
    """Filter, sort, and page tasks the same way the SDK in-memory store does."""
    selected = list(tasks)
    if params.context_id:
        selected = [task for task in selected if task.context_id == params.context_id]
    if params.status:
        selected = [task for task in selected if task.status.state == params.status]
    if params.HasField("status_timestamp_after"):
        after_ns = params.status_timestamp_after.ToNanoseconds()
        selected = [
            task
            for task in selected
            if (
                task.HasField("status")
                and task.status.HasField("timestamp")
                and task.status.timestamp.ToNanoseconds() >= after_ns
            )
        ]

    selected.sort(key=_list_sort_key, reverse=True)
    total_size = len(selected)
    start_idx = 0
    if params.page_token:
        cursor = decode_list_tasks_cursor(params.page_token)
        if cursor is None:
            start_idx = _legacy_start_index(selected, params.page_token)
        else:
            after = cursor.sort_key()
            start_idx = next(
                (index for index, task in enumerate(selected) if _list_sort_key(task) < after),
                total_size,
            )
    page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
    end_idx = start_idx + page_size
    next_page_token = (
        encode_list_tasks_cursor(_cursor_for(selected[end_idx - 1])) if end_idx < total_size else ""
    )
    page = selected[start_idx:end_idx]
    return ListTasksResponse(
        next_page_token=next_page_token,
        tasks=page,
        total_size=total_size,
        page_size=page_size,
    )


def _legacy_start_index(tasks: Sequence[Task], page_token: str) -> int:
    """Resolve a legacy page token, which names the first task of the page."""
    start_task_id = decode_page_token(page_token)
    for index, task in enumerate(tasks):
        if task.id == start_task_id:
            return index
    raise InvalidParamsError(f"Invalid page token: {page_token}")


class PrimrTaskStore(TaskStore):
    """A2A TaskStore backed by Primr's SingleJobStore.

    Maintains a mapping from A2A task IDs to Primr job IDs.
    Thread-safe via lock.
    """

    def __init__(self, job_store: SingleJobStore):
        self._job_store = job_store
        self._mappings: dict[str, A2ATaskMapping] = {}
        self._lock = threading.Lock()

    def register_mapping(self, mapping: A2ATaskMapping) -> None:
        """Register a task-to-job mapping."""
        with self._lock:
            self._mappings[mapping.task_id] = mapping
            logger.debug(
                "Registered A2A task mapping: %s -> job %s",
                mapping.task_id,
                mapping.job_id,
            )

    def get_mapping(self, task_id: str) -> A2ATaskMapping | None:
        """Get the mapping for an A2A task ID."""
        with self._lock:
            return self._mappings.get(task_id)

    def get_job_id(self, task_id: str) -> str | None:
        """Get the Primr job ID for an A2A task ID."""
        mapping = self.get_mapping(task_id)
        return mapping.job_id if mapping else None

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        """Get a task by ID, translating from Primr job state."""
        mapping = self.get_mapping(task_id)
        if not mapping:
            return None

        job = self._job_store.get(mapping.job_id)
        if not job:
            return None
        if context_client_id(context) != job.owner_client_id:
            return None

        return _job_to_task(job, task_id, context_id=task_id)

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """List tasks the caller owns, using the SDK page-token format."""
        with self._lock:
            task_ids = list(self._mappings)
        tasks: list[Task] = []
        for task_id in task_ids:
            task = await self.get(task_id, context)
            if task is not None:
                tasks.append(task)
        return _page_tasks(tasks, params)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Save is a no-op - Primr job store is the source of truth."""
        del context
        logger.debug("PrimrTaskStore.save called for task %s (no-op)", task.id)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Remove a task mapping."""
        del context
        with self._lock:
            self._mappings.pop(task_id, None)
            logger.debug("Removed A2A task mapping: %s", task_id)
