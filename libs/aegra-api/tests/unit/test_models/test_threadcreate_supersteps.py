"""Tests for ThreadCreate.supersteps validation.

Supersteps mirror the LangGraph SDK ``ThreadsClient.create(supersteps=...)``
contract used for cross-deployment thread migration. Each superstep contains
a sequence of updates ``{values, command, as_node, task_id?}`` that the server
applies via ``Pregel.abulk_update_state``. We validate payload shape here so
clients see structural errors as 422 at request time rather than 500 from
``abulk_update_state`` downstream.
"""

import pytest
from pydantic import ValidationError

from aegra_api.models.threads import ThreadCreate


class TestThreadCreateSupersteps:
    """Tests for ``ThreadCreate.supersteps`` shape enforcement."""

    def test_supersteps_defaults_to_none(self):
        thread = ThreadCreate(metadata={"graph_id": "agent"})
        assert thread.supersteps is None

    def test_accepts_single_superstep_with_values(self):
        payload = [{"updates": [{"values": {"messages": [{"role": "user", "content": "hi"}]}, "as_node": "model"}]}]
        thread = ThreadCreate(supersteps=payload)
        assert thread.supersteps == payload

    def test_accepts_command_field_in_update(self):
        """SDK contract includes ``command`` field; we accept it in the payload
        for drop-in compat even though ``StateUpdate`` has no command slot."""
        payload = [{"updates": [{"values": None, "command": {"resume": "yes"}, "as_node": "interrupt"}]}]
        thread = ThreadCreate(supersteps=payload)
        assert thread.supersteps[0]["updates"][0]["command"] == {"resume": "yes"}

    def test_accepts_optional_task_id(self):
        payload = [{"updates": [{"values": {"k": 1}, "as_node": "model", "task_id": "task-x"}]}]
        thread = ThreadCreate(supersteps=payload)
        assert thread.supersteps[0]["updates"][0]["task_id"] == "task-x"

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
