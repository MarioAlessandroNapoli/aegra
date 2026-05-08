"""Tests for ThreadCreate.supersteps validation.

Supersteps mirror the LangGraph SDK ``ThreadsClient.create(supersteps=...)``
contract used for cross-deployment thread migration. Each update carries
``values``/``as_node``/optional ``task_id`` that the server applies via
``Pregel.abulk_update_state``. ``command``-based updates are rejected at
the model boundary because ``StateUpdate`` has no slot for them — accepting
silently would lose data. Outer-list/outer-dict types are enforced by
Pydantic's coercion before our validator runs.
"""

import pytest
from pydantic import ValidationError

from aegra_api.models.threads import ThreadCreate


class TestThreadCreateSupersteps:
    """Tests for ``ThreadCreate.supersteps`` shape enforcement."""

    def test_supersteps_defaults_to_none(self):
        thread = ThreadCreate(metadata={"graph_id": "agent"})
        assert thread.supersteps is None

    def test_accepts_empty_list(self):
        """Empty supersteps is a no-op: helper short-circuits, no error."""
        thread = ThreadCreate(supersteps=[])
        assert thread.supersteps == []

    def test_accepts_single_superstep_with_values(self):
        payload = [{"updates": [{"values": {"messages": [{"role": "user", "content": "hi"}]}, "as_node": "model"}]}]
        thread = ThreadCreate(supersteps=payload)
        assert thread.supersteps == payload

    def test_accepts_values_none_for_checkpoint_anchor(self):
        """``values=None`` is legal for `__copy__` fork anchoring (LangGraph Studio re-run)."""
        payload = [{"updates": [{"values": None, "as_node": "__copy__"}]}]
        thread = ThreadCreate(supersteps=payload)
        assert thread.supersteps[0]["updates"][0]["values"] is None

    def test_accepts_optional_task_id(self):
        payload = [{"updates": [{"values": {"k": 1}, "as_node": "model", "task_id": "task-x"}]}]
        thread = ThreadCreate(supersteps=payload)
        assert thread.supersteps[0]["updates"][0]["task_id"] == "task-x"

    def test_rejects_command_field_present(self):
        """``command`` updates rejected: StateUpdate has no slot for command."""
        payload = [{"updates": [{"values": None, "command": {"resume": "yes"}, "as_node": "interrupt"}]}]
        with pytest.raises(ValidationError, match="command-based updates are not supported"):
            ThreadCreate(supersteps=payload)

    def test_rejects_superstep_not_object(self):
        # Pydantic's built-in type check rejects non-dict before our validator runs.
        with pytest.raises(ValidationError, match="dictionary|expected object"):
            ThreadCreate(supersteps=["not-a-dict"])

    def test_rejects_updates_not_list(self):
        with pytest.raises(ValidationError, match="expected array"):
            ThreadCreate(supersteps=[{"updates": "not-a-list"}])

    def test_rejects_update_missing_as_node(self):
        with pytest.raises(ValidationError, match="'as_node' required"):
            ThreadCreate(supersteps=[{"updates": [{"values": {"k": 1}}]}])

    def test_rejects_too_many_supersteps(self):
        payload = [{"updates": [{"values": {}, "as_node": "model"}]}] * 1001
        with pytest.raises(ValidationError, match="max 1000 supersteps"):
            ThreadCreate(supersteps=payload)

    def test_rejects_too_many_updates_per_superstep(self):
        updates = [{"values": {}, "as_node": "model"}] * 1001
        with pytest.raises(ValidationError, match="max 1000 updates per superstep"):
            ThreadCreate(supersteps=[{"updates": updates}])

    def test_rejects_update_not_object(self):
        with pytest.raises(ValidationError, match="expected object"):
            ThreadCreate(supersteps=[{"updates": ["not-a-dict"]}])
