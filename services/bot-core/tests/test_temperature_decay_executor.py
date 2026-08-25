"""Tests for the retired temperature_decay executor (rev 0031 — no-op handler).

The temperature subsystem was removed in rev 0031.  The executor is now a
no-op shim that logs a deprecation warning and returns immediately.  These
tests verify that stale ``temperature_decay`` job rows do not error-spam
the scheduler log.
"""

from utils.executors.temperature_decay_executor import execute_temperature_decay_job


class TestTemperatureDecayExecutorNoOp:
    """Retired temperature_decay executor: must not raise and must return deprecated status."""

    async def test_noop_returns_deprecated_status(self):
        """The no-op handler returns status='deprecated'."""
        result = await execute_temperature_decay_job("test-job-id", {"job_type": "temperature_decay"})
        assert result["status"] == "deprecated"

    async def test_noop_returns_job_id(self):
        """The no-op handler echoes back the job_id."""
        result = await execute_temperature_decay_job("some-job-id", {"job_type": "temperature_decay"})
        assert result["job_id"] == "some-job-id"

    async def test_noop_does_not_raise(self):
        """The no-op handler never raises regardless of payload content."""
        # Empty payload
        await execute_temperature_decay_job("j1", {})
        # Arbitrary payload
        await execute_temperature_decay_job("j2", {"guild_id": 12345, "division": "bronze"})

    async def test_noop_message_mentions_retirement(self):
        """The no-op response message describes the retirement."""
        result = await execute_temperature_decay_job("x", {})
        assert "retired" in result["message"].lower() or "deprecated" in result.get("message", "").lower()
