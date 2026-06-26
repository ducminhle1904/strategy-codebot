# AI Agent Log Trace

Strategy Codebot emits AI-agent request logs as one-line logfmt records on stdout.

Use `trace_id` as the primary correlation key across Next.js, CopilotKit, Python API, run events, and artifacts:

```bash
docker compose logs web api | rg 'trace_id=trace_abc'
```

Fallback to request/client ids when needed:

```bash
docker compose logs web api | rg 'request_id=req_abc|client_request_id=req_abc'
```

Example:

```text
ts=2026-06-25T11:52:10.123Z lvl=info svc=web component=copilotkit event=copilot.run.requested trace_id=trace_abc request_id=req_abc conversation_id=conv_123 thread_id=conv_123 message_count=4 web_search=auto
ts=2026-06-25T11:52:10.430Z lvl=info svc=api component=llm_orchestrator event=agent.action_plan trace_id=trace_abc request_id=req_abc conversation_id=conv_123 run_id=run_456 decision=call_tool tool_id=query_backtest_trades confidence=0.94
ts=2026-06-25T11:52:10.810Z lvl=info svc=web component=copilotkit event=copilot.run.finished trace_id=trace_abc request_id=req_abc copilot_run_id=run_client agui_events=18 custom_event_count=4 text_deltas=2 duration_ms=687 status=success
```

Logs intentionally do not include full prompts, message content, tool output, secrets, cookies, or API keys. Use run events and artifacts for detailed audit payloads.
