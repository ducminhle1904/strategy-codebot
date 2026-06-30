import re

from fastapi.testclient import TestClient

from strategy_codebot.server import ServerAppConfig, create_app
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.repository import InMemoryConversationRepository


AUTH_A = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-a"}
AUTH_B = {"X-User-Id": "user-b", "X-Workspace-Id": "workspace-a"}
AUTH_OTHER_WORKSPACE = {"X-User-Id": "user-a", "X-Workspace-Id": "workspace-b"}
AUTH_FREE = {"X-User-Id": "user-free", "X-Workspace-Id": "workspace-free", "X-User-Tier": "free"}


def test_health_is_public() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "strategy-codebot-api"
    assert payload["version"]


def test_local_frontend_origin_can_call_api_with_auth_headers() -> None:
    client = TestClient(create_app())

    for origin in (
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://0.0.0.0:3000",
        "http://192.168.1.25:3000",
    ):
        response = client.options(
            "/v1/conversations/sidebar",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-user-id,x-workspace-id",
            },
        )

        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == origin
        assert "x-user-id" in response.headers["access-control-allow-headers"].lower()
        assert "x-workspace-id" in response.headers["access-control-allow-headers"].lower()


def test_create_app_accepts_config_dataclass() -> None:
    repository = InMemoryConversationRepository()
    client = TestClient(create_app(config=ServerAppConfig(repository=repository)))

    response = client.post("/v1/conversations", headers=AUTH_A, json={"title": "configured"})

    assert response.status_code == 201, response.text
    conversations = repository.list_conversations(AuthContext("user-a", "workspace-a"))
    assert [conversation.title for conversation in conversations] == ["configured"]


def test_create_app_legacy_kwargs_override_config() -> None:
    configured_repository = InMemoryConversationRepository()
    override_repository = InMemoryConversationRepository()
    client = TestClient(
        create_app(
            config=ServerAppConfig(repository=configured_repository),
            repository=override_repository,
        )
    )

    response = client.post("/v1/conversations", headers=AUTH_A, json={"title": "override"})

    assert response.status_code == 201, response.text
    assert configured_repository.list_conversations(AuthContext("user-a", "workspace-a")) == []
    conversations = override_repository.list_conversations(AuthContext("user-a", "workspace-a"))
    assert [conversation.title for conversation in conversations] == ["override"]


def test_v1_routes_require_auth_headers() -> None:
    client = TestClient(create_app())

    assert client.get("/v1/conversations").status_code == 401
    assert client.post("/v1/conversations", json={}).status_code == 401


def test_me_returns_workspace_capability() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/me", headers={**AUTH_A, "X-User-Tier": "paid_medium"})

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["user"]["id"] == "user-a"
    assert payload["workspace"]["id"] == "workspace-a"
    assert payload["capability"]["tier"] == "paid_medium"
    assert payload["capability"]["tier_label"] == "Pro"
    assert payload["capability"]["allowed_message_modes"] == ["deterministic", "agent"]
    assert payload["capability"]["capability_matrix"]["dry-run"]["status"] == "available"


def test_provider_status_does_not_expose_secrets() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/provider/status", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tier"] == "paid_low"
    assert "allowed_run_modes" in payload
    assert "capability_matrix" in payload
    assert payload["capability_matrix"]["dry-run"]["status"] == "available"
    assert "model_routing_mode" in payload
    assert "route_ready" in payload
    assert "user_message" in payload
    assert "api_key" not in str(payload).lower()
    assert "secret" not in str(payload).lower()


def test_provider_status_defaults_to_registry_routing(monkeypatch) -> None:
    monkeypatch.delenv("STRATEGY_CODEBOT_LLM_ROUTING", raising=False)
    client = TestClient(create_app())

    response = client.get("/v1/provider/status", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_routing_mode"] == "registry"


def test_action_registry_exposes_backend_action_metadata() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/action-registry", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    tool_ids = {action["tool_id"] for action in payload["actions"]}
    assert "build_robustness_report" in tool_ids
    assert "query_backtest_trades" in tool_ids
    robustness = next(action for action in payload["actions"] if action["tool_id"] == "build_robustness_report")
    assert robustness["artifact_kind"] == "robustness_report"
    assert all(action.get("presentation", {}).get("icon_key") for action in payload["actions"])
    assert all(action.get("presentation", {}).get("badge_key") for action in payload["actions"])
    assert all(action.get("presentation", {}).get("visibility_key") for action in payload["actions"])


def test_provider_status_returns_user_safe_registry_routing_fields(monkeypatch) -> None:
    monkeypatch.setenv("STRATEGY_CODEBOT_LLM_ROUTING", "registry")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
    client = TestClient(create_app())

    response = client.get("/v1/provider/status", headers=AUTH_A)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["model_routing_mode"] == "registry"
    assert payload["model_tier"] == "paid_low"
    assert payload["selected_stage_defaults"]["strategy_reasoning"] in {
        "Managed model route",
        "OpenRouter route",
        "Vercel AI Gateway route",
        "OpenAI route",
        "Configured model route",
    }
    assert "openrouter" in payload["available_gateways"]
    assert isinstance(payload["fallback_enabled"], bool)
    serialized = str(payload)
    assert "OPENROUTER_API_KEY" not in serialized
    assert "test-openrouter-key" not in serialized
    assert "paid_low.strategy_reasoning" not in serialized


def test_free_tier_can_use_backend_routed_agent_message_mode() -> None:
    client = TestClient(create_app())
    conversation = client.post("/v1/conversations", headers=AUTH_FREE, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages?stream=true&mode=agent",
        headers=AUTH_FREE,
        json={"content": "hello"},
    )

    assert response.status_code in {200, 503}


def test_account_usage_empty_summary_is_user_facing_zero() -> None:
    client = TestClient(create_app())

    response = client.get("/v1/account/usage", headers=AUTH_FREE)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["tier"] == "free"
    assert payload["tier_label"] == "Free"
    assert payload["messages"] == 0
    assert payload["runs"] == 0
    assert payload["artifacts"] == 0
    assert payload["input_tokens"] == 0
    assert payload["output_tokens"] == 0
    assert payload["total_tokens"] == 0
    assert payload["estimated_cost_usd"] is None
    assert payload["period_start"] < payload["period_end"]


def test_account_usage_is_scoped_to_current_tenant() -> None:
    repository = InMemoryConversationRepository()
    auth = AuthContext("user-a", "workspace-a")
    other_auth = AuthContext("user-b", "workspace-a")
    conversation = repository.create_conversation(auth)
    repository.create_message(auth, conversation.id, "hello")
    run = repository.create_run(auth, conversation.id)
    assert run is not None
    repository.create_artifact(
        auth,
        run.id,
        kind="pine_file",
        mime_type="text/plain",
        display_name="strategy.pine",
        storage_key="artifacts/strategy.pine",
    )
    repository.create_usage_ledger(
        auth,
        run_id=run.id,
        model="model-a",
        tool_id=None,
        input_tokens=12,
        output_tokens=8,
        cost_estimate_usd=0.002,
    )
    other_conversation = repository.create_conversation(other_auth)
    repository.create_message(other_auth, other_conversation.id, "hidden")
    client = TestClient(create_app(repository=repository))

    payload = client.get("/v1/account/usage", headers=AUTH_A).json()
    other_payload = client.get("/v1/account/usage", headers=AUTH_B).json()

    assert payload["messages"] == 1
    assert payload["runs"] == 1
    assert payload["artifacts"] == 1
    assert payload["input_tokens"] == 12
    assert payload["output_tokens"] == 8
    assert payload["total_tokens"] == 20
    assert payload["estimated_cost_usd"] == 0.002
    assert other_payload["messages"] == 1
    assert other_payload["runs"] == 0
    assert other_payload["artifacts"] == 0
    assert other_payload["total_tokens"] == 0


def test_conversation_create_list_and_get_are_scoped_to_auth_context() -> None:
    client = TestClient(create_app())

    create_response = client.post("/v1/conversations", headers=AUTH_A, json={"title": "  Breakout plan  "})

    assert create_response.status_code == 201, create_response.text
    conversation = create_response.json()
    assert re.fullmatch(r"conv_[0-9a-f]{32}", conversation["id"])
    assert conversation["owner_user_id"] == "user-a"
    assert conversation["workspace_id"] == "workspace-a"
    assert conversation["title"] == "Breakout plan"
    assert "path" not in conversation

    list_response = client.get("/v1/conversations", headers=AUTH_A)
    assert list_response.status_code == 200
    assert list_response.json()["items"] == [conversation]

    get_response = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A)
    assert get_response.status_code == 200
    assert get_response.json() == conversation


def test_conversation_rename_updates_title_and_blocks_cross_tenant() -> None:
    client = TestClient(create_app())
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Old title"}).json()

    renamed = client.patch(
        f"/v1/conversations/{conversation['id']}",
        headers=AUTH_A,
        json={"title": "  New title  "},
    )
    cross_user = client.patch(
        f"/v1/conversations/{conversation['id']}",
        headers=AUTH_B,
        json={"title": "Blocked"},
    )
    blank = client.patch(
        f"/v1/conversations/{conversation['id']}",
        headers=AUTH_A,
        json={"title": "   "},
    )

    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["title"] == "New title"
    assert renamed.json()["updated_at"] >= conversation["updated_at"]
    assert cross_user.status_code == 404
    assert blank.status_code == 422


def test_conversation_delete_soft_removes_chat_from_api_surfaces() -> None:
    client = TestClient(create_app())
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Delete me"}).json()
    other = client.post("/v1/conversations", headers=AUTH_A, json={"title": "Keep me"}).json()

    deleted = client.delete(f"/v1/conversations/{conversation['id']}", headers=AUTH_A)

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["id"] == conversation["id"]
    assert client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).status_code == 404
    assert client.get(f"/v1/conversations/{conversation['id']}/state", headers=AUTH_A).status_code == 404
    assert client.get(f"/v1/conversations/{conversation['id']}/messages", headers=AUTH_A).status_code == 404
    list_items = client.get("/v1/conversations", headers=AUTH_A).json()["items"]
    sidebar_items = client.get("/v1/conversations/sidebar", headers=AUTH_A).json()["items"]
    assert [item["id"] for item in list_items] == [other["id"]]
    assert [item["conversation"]["id"] for item in sidebar_items] == [other["id"]]
    assert client.delete(f"/v1/conversations/{conversation['id']}", headers=AUTH_A).status_code == 404


def test_conversation_list_is_sorted_by_newest_update() -> None:
    client = TestClient(create_app())

    older = client.post("/v1/conversations", headers=AUTH_A, json={"title": "older"}).json()
    newer = client.post("/v1/conversations", headers=AUTH_A, json={"title": "newer"}).json()
    message_response = client.post(
        f"/v1/conversations/{older['id']}/messages",
        headers=AUTH_A,
        json={"content": "move older to top"},
    )

    assert message_response.status_code == 201, message_response.text
    items = client.get("/v1/conversations", headers=AUTH_A).json()["items"]
    assert [item["id"] for item in items] == [older["id"], newer["id"]]


def test_cross_user_and_workspace_reads_return_404() -> None:
    client = TestClient(create_app())
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    cross_user = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_B)
    cross_workspace = client.get(f"/v1/conversations/{conversation['id']}", headers=AUTH_OTHER_WORKSPACE)

    assert cross_user.status_code == 404
    assert cross_workspace.status_code == 404
    assert client.get("/v1/conversations", headers=AUTH_B).json()["items"] == []
    assert client.get("/v1/conversations", headers=AUTH_OTHER_WORKSPACE).json()["items"] == []


def test_cross_user_and_workspace_message_creation_returns_404_without_writing() -> None:
    repository = InMemoryConversationRepository()
    client = TestClient(create_app(repository=repository))
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    cross_user = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_B,
        json={"content": "unauthorized"},
    )
    cross_workspace = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_OTHER_WORKSPACE,
        json={"content": "unauthorized"},
    )

    assert cross_user.status_code == 404
    assert cross_workspace.status_code == 404
    assert repository.list_messages(AuthContext("user-a", "workspace-a"), conversation["id"]) == []


def test_message_creation_stores_user_role_and_content() -> None:
    client = TestClient(create_app())
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    response = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "Draft a Pine strategy"},
    )

    assert response.status_code == 201, response.text
    message = response.json()
    assert re.fullmatch(r"msg_[0-9a-f]{32}", message["id"])
    assert message["conversation_id"] == conversation["id"]
    assert message["owner_user_id"] == "user-a"
    assert message["workspace_id"] == "workspace-a"
    assert message["role"] == "user"
    assert message["content"] == "Draft a Pine strategy"
    assert "path" not in message


def test_empty_message_content_is_rejected() -> None:
    client = TestClient(create_app())
    conversation = client.post("/v1/conversations", headers=AUTH_A, json={}).json()

    empty = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": ""},
    )
    blank = client.post(
        f"/v1/conversations/{conversation['id']}/messages",
        headers=AUTH_A,
        json={"content": "   "},
    )

    assert empty.status_code == 422
    assert blank.status_code == 422
