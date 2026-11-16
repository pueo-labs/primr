"""Shared A2A task status event construction."""

from a2a.helpers import (
    new_task,
    new_text_status_update_event,
)
from a2a.helpers import new_text_message as new_agent_text_message
from a2a.types import Message, Task, TaskState, TaskStatusUpdateEvent


def status_message(text: str, task_id: str | None, context_id: str | None) -> Message:
    return new_agent_text_message(text, task_id=task_id, context_id=context_id)


def new_opening_task(task_id: str, context_id: str) -> Task:
    """Task that must precede the first status event on a protocol 1.0 stream."""
    return new_task(task_id, context_id, TaskState.TASK_STATE_SUBMITTED)


def status_update_event(
    *,
    state: TaskState,
    text: str,
    task_id: str,
    context_id: str,
    final: bool,
) -> TaskStatusUpdateEvent:
    """Build a status event.

    ``final`` stays on the helper so callers can keep marking the last
    event they intend to send. Protocol 1.0 has no ``final`` field and
    infers finality from the stream ending.
    """
    del final
    return new_text_status_update_event(task_id, context_id, state, text)


__all__ = ["new_opening_task", "status_message", "status_update_event"]
