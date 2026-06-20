import os
from dataclasses import dataclass

from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.repository import ConversationRepository
from strategy_codebot.server.repository import MessageRecord
from strategy_codebot.server.token_estimation import estimate_tokens

CONTEXT_HISTORY_LIMIT = 80
RECENT_MESSAGE_TARGET = 12
DEFAULT_CONTEXT_WINDOW_TOKENS = 32000
MIN_OUTPUT_RESERVE_TOKENS = 2000
COMPACTION_THRESHOLD_TOKENS = 12000
COMPACTION_THRESHOLD_MESSAGES = 20


@dataclass(frozen=True)
class ConversationContext:
    messages: list[dict[str, str]]
    summary_used: bool
    history_message_count: int
    estimated_input_tokens: int
    truncated: bool
    prior_context_text: str


class ConversationContextBuilder:
    def __init__(self, repository: ConversationRepository) -> None:
        self.repository = repository

    def build(
        self,
        *,
        auth: AuthContext,
        conversation_id: str,
        current_message_id: str | None,
        current_user_message: str,
        system_prompt: str,
    ) -> ConversationContext:
        memory = self.repository.get_conversation_memory(auth, conversation_id)
        raw_history = self.repository.list_messages_for_context(auth, conversation_id, limit=CONTEXT_HISTORY_LIMIT)
        history = _context_messages(raw_history, current_message_id=current_message_id)
        budget = _input_budget_tokens()

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if memory is not None and memory.summary.strip():
            messages.append({"role": "system", "content": _memory_prompt(memory.summary)})

        recent = _fit_recent_messages(
            history,
            current_user_message=current_user_message,
            base_tokens=sum(estimate_tokens(message["content"]) for message in messages),
            budget_tokens=budget,
        )
        messages.extend(recent)
        messages.append({"role": "user", "content": current_user_message})
        prior_context_text = "\n".join(
            [
                *(["memory: " + memory.summary.strip()] if memory is not None and memory.summary.strip() else []),
                *(f"{message['role']}: {message['content']}" for message in recent),
            ]
        )

        estimated = sum(estimate_tokens(message["content"]) for message in messages)
        return ConversationContext(
            messages=messages,
            summary_used=memory is not None and bool(memory.summary.strip()),
            history_message_count=len(history),
            estimated_input_tokens=estimated,
            truncated=len(recent) < len(history),
            prior_context_text=prior_context_text,
        )

    def should_compact(self, *, auth: AuthContext, conversation_id: str) -> bool:
        messages = self._messages_for_compaction(auth=auth, conversation_id=conversation_id)
        if len(messages) >= _compaction_threshold_messages():
            return True
        return sum(estimate_tokens(message["content"]) for message in messages) >= _compaction_threshold_tokens()

    def build_summary_messages(
        self,
        *,
        auth: AuthContext,
        conversation_id: str,
        language: str,
    ) -> tuple[list[dict[str, str]], str | None, int]:
        memory = self.repository.get_conversation_memory(auth, conversation_id)
        messages = self._messages_for_compaction(auth=auth, conversation_id=conversation_id)
        if not messages:
            return [], None, 0
        covered_message_id = messages[-1]["id"]
        transcript_parts = []
        if memory is not None and memory.summary.strip():
            transcript_parts.append(f"previous_memory_summary: {memory.summary.strip()}")
        transcript_parts.extend(f"{message['role']}: {message['content']}" for message in messages)
        transcript = "\n".join(transcript_parts)
        prompt = _summary_prompt(transcript, language=language)
        return [
            {"role": "system", "content": "Summarize conversation memory for future model context. Do not answer the user."},
            {"role": "user", "content": prompt},
        ], covered_message_id, estimate_tokens(prompt)

    def _messages_for_compaction(self, *, auth: AuthContext, conversation_id: str) -> list[dict[str, str]]:
        memory = self.repository.get_conversation_memory(auth, conversation_id)
        messages = _context_messages(self.repository.list_messages_for_context(auth, conversation_id, limit=None), current_message_id=None)
        if memory is None or memory.covered_message_id is None:
            return messages
        for index, message in enumerate(messages):
            if message["id"] == memory.covered_message_id:
                return messages[index + 1 :]
        return messages


def _context_messages(messages: list[MessageRecord], *, current_message_id: str | None) -> list[dict[str, str]]:
    context: list[dict[str, str]] = []
    for message in messages:
        if current_message_id is not None and message.id == current_message_id:
            continue
        if message.role not in {"user", "assistant"}:
            continue
        content = message.content.strip()
        if not content:
            continue
        context.append({"id": message.id, "role": message.role, "content": content})
    return context


def _fit_recent_messages(
    history: list[dict[str, str]],
    *,
    current_user_message: str,
    base_tokens: int,
    budget_tokens: int,
) -> list[dict[str, str]]:
    reserve = estimate_tokens(current_user_message)
    remaining = max(0, budget_tokens - base_tokens - reserve)
    selected: list[dict[str, str]] = []
    used = 0
    for message in reversed(history[-RECENT_MESSAGE_TARGET:]):
        cost = estimate_tokens(message["content"])
        if selected and used + cost > remaining:
            break
        if not selected and cost > remaining:
            break
        selected.append({"role": message["role"], "content": message["content"]})
        used += cost
    return list(reversed(selected))


def _memory_prompt(summary: str) -> str:
    return (
        "Conversation memory summary for continuity. Treat this as internal context, not as a user-visible message.\n\n"
        f"{summary.strip()}"
    )


def _summary_prompt(transcript: str, *, language: str) -> str:
    response_language = "Vietnamese" if language == "vi" else "English"
    return (
        f"Create a concise internal memory summary in {response_language} for this Strategy Codebot conversation.\n"
        "Preserve only durable context needed for future turns:\n"
        "- user intent and current task\n"
        "- strategy facts, market/timeframe, entry/exit/risk rules\n"
        "- decisions already made\n"
        "- artifact references or requested next steps\n"
        "- open questions\n"
        "- review-only product boundaries\n\n"
        "Do not include secrets, raw traces, provider details, or observability metadata.\n\n"
        f"Transcript:\n{transcript}"
    )


def _input_budget_tokens() -> int:
    context_window = _positive_int_env("STRATEGY_CODEBOT_MODEL_CONTEXT_WINDOW_TOKENS", DEFAULT_CONTEXT_WINDOW_TOKENS)
    reserve = max(MIN_OUTPUT_RESERVE_TOKENS, context_window // 4)
    return max(1024, context_window - reserve)


def _compaction_threshold_tokens() -> int:
    return _positive_int_env("STRATEGY_CODEBOT_CONVERSATION_COMPACTION_THRESHOLD_TOKENS", COMPACTION_THRESHOLD_TOKENS)


def _compaction_threshold_messages() -> int:
    return _positive_int_env("STRATEGY_CODEBOT_CONVERSATION_COMPACTION_THRESHOLD_MESSAGES", COMPACTION_THRESHOLD_MESSAGES)


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
