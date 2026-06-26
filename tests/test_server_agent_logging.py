import logging

from strategy_codebot.server.agent_logging import agent_log, format_logfmt


def test_format_logfmt_escapes_values_and_redacts_sensitive_fields() -> None:
    line = format_logfmt(
        {
            "event": "agent.action_plan",
            "trace_id": "trace_1",
            "label": 'hello "world"\nnext',
            "error": "provider returned sk-proj-12345678901234567890",
            "message_content": "do not log this prompt",
            "api_key": "secret",
        }
    )

    assert line == (
        'event=agent.action_plan trace_id=trace_1 label="hello \\"world\\" next" '
        "error=\"provider returned [REDACTED]\" "
        'message_content="[redacted len=22]" api_key=[redacted]'
    )


def test_agent_log_emits_single_logfmt_line(caplog) -> None:
    logger = logging.getLogger("strategy_codebot.tests.agent_log")

    with caplog.at_level(logging.INFO):
        agent_log(
            logger,
            "info",
            "tool.completed",
            component="llm_orchestrator",
            trace_id="trace_1",
            request_id="req_1",
        )

    assert len(caplog.records) == 1
    assert "svc=api component=llm_orchestrator event=tool.completed" in caplog.records[0].message
    assert "\n" not in caplog.records[0].message
