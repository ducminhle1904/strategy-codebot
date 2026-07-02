from dataclasses import dataclass

from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.market_data import MarketDataGateway
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.security_controls import RunBudgetConfig
from strategy_codebot.server.security_controls import SecurityControls


@dataclass(frozen=True)
class OrchestratorServicePorts:
    repository: ConversationRepository
    artifact_store: LocalArtifactStore
    client: LLMClient
    security_controls: SecurityControls
    budget_config: RunBudgetConfig
    market_data_gateway: MarketDataGateway | None
