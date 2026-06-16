from strategy_codebot.harness import build_trace_command, harness_outcome, should_record_harness


def test_harness_trace_command_shape() -> None:
    command = build_trace_command(
        summary="Test run",
        story="US-001",
        agent="pine_specialist",
        outcome="pass",
        changed=["runs/test/validation-report.json"],
        notes="unit test",
    )

    assert command[1:4] == ["trace", "--summary", "Test run"]
    assert "--story" in command
    assert "US-001" in command
    assert "--changed" in command


def test_explicit_no_record_harness_wins() -> None:
    assert should_record_harness(False) is False


def test_harness_outcome_maps_validation_status() -> None:
    assert harness_outcome("pass") == "completed"
    assert harness_outcome("fail") == "failed"
    assert harness_outcome("manual_required") == "partial"
