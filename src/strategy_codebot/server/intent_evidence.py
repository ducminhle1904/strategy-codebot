from __future__ import annotations

import re


def collect_chat_regex_evidence(message_content: str) -> dict[str, bool]:
    normalized = _normalize(message_content)
    return {
        "explicit_backtest": explicit_backtest_signal(normalized),
        "preview_intent": preview_intent_signal(normalized),
        "pine_or_code": pine_or_code_signal(normalized),
        "current_info": current_context_signal(message_content),
        "artifact_or_strategy": artifact_or_strategy_signal(normalized),
        "risky_url_action": risky_url_action_signal(normalized),
        "market_snapshot": market_snapshot_signal(normalized),
        "docs_research": docs_research_signal(normalized),
        "market_research": market_research_signal(normalized),
        "strategy_design_request": strategy_design_request_signal(normalized),
        "paper_bot_simulation_request": paper_bot_simulation_request_signal(normalized),
    }


def _normalize(message_content: str) -> str:
    return " ".join((message_content or "").lower().split())


def artifact_or_strategy_signal(normalized: str) -> bool:
    return artifact_generation_signal(normalized) or strategy_terms_signal(normalized)


def artifact_generation_signal(normalized: str) -> bool:
    artifact_terms = (
        "artifact",
        "code",
        "pine",
        "mql5",
        "script",
        "ea",
        "expert advisor",
        "generate",
        "gen ",
        "create",
        "tạo",
        "viết code",
        "sinh code",
    )
    strategy_terms = ("strategy", "chiến lược", "indicator", "review", "spec")
    return any(term in normalized for term in artifact_terms) and any(
        term in normalized for term in strategy_terms
    )


def strategy_terms_signal(normalized: str) -> bool:
    strategy_terms = (
        "strategy",
        "chiến lược",
        "entry",
        "exit",
        "stop loss",
        "take profit",
        "risk",
        "timeframe",
        "ema",
        "sma",
        "rsi",
        "breakout",
        "liquidity",
    )
    return any(term in normalized for term in strategy_terms)


def strategy_design_request_signal(normalized: str) -> bool:
    design_terms = (
        "build",
        "design",
        "draft",
        "create",
        "xây dựng",
        "thiết kế",
        "tạo",
        "lập",
    )
    return strategy_terms_signal(normalized) and any(term in normalized for term in design_terms)


def paper_bot_simulation_request_signal(normalized: str) -> bool:
    bot_terms = ("bot", "paper bot", "bot simulation", "paper trading")
    simulation_terms = (
        "simulation",
        "simulate",
        "paper",
        "paper trading",
        "theo dõi thử",
        "chạy thử",
        "mô phỏng",
    )
    return any(term in normalized for term in bot_terms) and any(
        term in normalized for term in simulation_terms
    )


def market_snapshot_signal(normalized: str) -> bool:
    asset_terms = (
        "btc",
        "bitcoin",
        "eth",
        "ethereum",
        "sol",
        "bnb",
        "xau",
        "gold",
        "forex",
        "usd",
        "usdt",
    )
    price_terms = ("price", "giá", "quote", "current", "today", "now", "hiện tại", "hôm nay", "bây giờ")
    return any(term in normalized for term in asset_terms) and any(term in normalized for term in price_terms)


def docs_research_signal(normalized: str) -> bool:
    doc_terms = (
        "docs",
        "documentation",
        "api",
        "sdk",
        "provider",
        "pricing",
        "version",
        "release",
        "tài liệu",
        "phiên bản",
    )
    return any(term in normalized for term in doc_terms)


def market_research_signal(normalized: str) -> bool:
    research_terms = ("research", "news", "latest", "sources", "citation", "tin tức", "nguồn", "nghiên cứu")
    market_terms = ("market", "crypto", "forex", "btc", "eth", "price", "giá")
    if any(term in normalized for term in research_terms) and any(term in normalized for term in market_terms):
        return True
    market_context_terms = (
        "market condition",
        "market conditions",
        "market setup",
        "market context",
        "market hiện tại",
    )
    market_followup_terms = (
        "what should",
        "what do i do",
        "what to do",
        "should i",
        "suitable",
        "plan",
        "condition",
        "nên làm gì",
        "làm gì",
        "phù hợp",
    )
    return any(term in normalized for term in market_context_terms) or (
        "market" in normalized and any(term in normalized for term in market_followup_terms)
    )


def explicit_backtest_signal(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(backtest|run\s+(?:a\s+)?preview|test(?:\s+(?:the\s+)?strategy)?|compare(?:\s+variants?)?)\b"
            r"|chạy\s+backtest|kiểm\s*thử|test\s+(?:chiến\s*lược|strategy)|so\s+sánh",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def preview_intent_signal(normalized: str) -> bool:
    terms = (
        "simulate",
        "paper test",
        "preview performance",
        "preview evidence",
        "run thử",
        "chạy thử",
        "thử hiệu quả",
        "xem chiến lược này ổn không",
        "chay thu",
        "thu hieu qua",
    )
    return any(term in normalized for term in terms)


def pine_or_code_signal(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(pine|pinescript|strategy\.entry|strategy\.exit|script|code|indicator|mql5|expert advisor)\b"
            r"|viết\s+code|sinh\s+code|tạo\s+code",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def risky_url_action_signal(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(run|execute|call|use|fetch|open|read|send|submit|request|connect|download|upload|curl|wget|shell|"
            r"filesystem|network\s+request)\b",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def current_context_signal(message_content: str) -> bool:
    normalized = message_content.lower()
    explicit_terms = (
        "latest",
        "recent",
        "research",
        "sources",
        "citation",
        "citations",
        "cite",
        "docs",
        "documentation",
        "provider",
        "pricing",
        "news",
        "release",
        "version",
        "web",
        "search",
        "mới nhất",
        "gần đây",
        "nghiên cứu",
        "tìm kiếm",
        "tài liệu",
        "nguồn",
        "tin tức",
    )
    if any(term in normalized for term in explicit_terms):
        return True

    qualified_patterns = (
        r"\b(current|today(?:'s)?|now|real[- ]?time|up[- ]?to[- ]?date)\s+"
        r"(price|prices|market data|docs?|documentation|provider|providers|model|models|pricing|release|version)s?\b",
        r"\b(current|today(?:'s)?|now|real[- ]?time|up[- ]?to[- ]?date)\b.{0,32}\b"
        r"(price|prices|market data|docs?|documentation|provider|providers|model|models|pricing|release|version)s?\b",
        r"\b(price|prices|market data|docs?|documentation|provider|providers|model|models|pricing|release|version)s?"
        r"\s+(today|now|currently|current)\b",
        r"(giá|market data|provider|model|phiên bản|release).{0,32}(hiện tại|hôm nay|bây giờ)",
        r"(hiện tại|hôm nay|bây giờ).{0,32}(giá|market data|provider|model|phiên bản|release)",
    )
    return any(re.search(pattern, normalized) for pattern in qualified_patterns)
