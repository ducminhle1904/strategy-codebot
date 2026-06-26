from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from strategy_codebot.knowledge_base import KNOWLEDGE_CANDIDATES_PATH
from strategy_codebot.knowledge_base import KNOWLEDGE_DATABASE_URL_ENV
from strategy_codebot.knowledge_base import KNOWLEDGE_INDEX_PATH
from strategy_codebot.knowledge_base import LEARNING_APPROVAL_MODES
from strategy_codebot.knowledge_base import approve_candidate as approve_knowledge_candidate
from strategy_codebot.knowledge_base import learn_from_run as learn_knowledge_from_run
from strategy_codebot.knowledge_base import load_candidates
from strategy_codebot.knowledge_base import propose_candidate as propose_knowledge_candidate
from strategy_codebot.knowledge_base import review_candidates_for_auto_promotion
from strategy_codebot.knowledge_base import reject_candidate as reject_knowledge_candidate
from strategy_codebot.server.artifact_store import LocalArtifactStore
from strategy_codebot.server.auth import AuthContext
from strategy_codebot.server.llm_clients import LLMClient
from strategy_codebot.server.llm_clients import LLM_EVENT_MESSAGE_DELTA
from strategy_codebot.server.llm_json import extract_json_object
from strategy_codebot.server.model_routing import MODEL_STAGE_KNOWLEDGE_LEARNING_REVIEW
from strategy_codebot.server.redaction import redact_value
from strategy_codebot.server.repository import AssistantRunRecord
from strategy_codebot.server.repository import ConversationRepository

KNOWLEDGE_INDEX_ENV = "STRATEGY_CODEBOT_KNOWLEDGE_INDEX"
KNOWLEDGE_CANDIDATES_ENV = "STRATEGY_CODEBOT_KNOWLEDGE_CANDIDATES_PATH"
KNOWLEDGE_AUTO_CANDIDATES_ENV = "STRATEGY_CODEBOT_KNOWLEDGE_AUTO_CANDIDATES_ENABLED"
KNOWLEDGE_ADMIN_ROLES = {"owner", "admin"}
RUN_EVIDENCE_RE = re.compile(r"\brun[:/](run[-_][A-Za-z0-9_-]+)\b")
LEARNING_ARTIFACT_NAMES = {
    "eval-report.json",
    "backtest-report.json",
    "intelligence-report.json",
    "context-report.json",
    "latency-report.json",
    "proxy-log-report.json",
}


def knowledge_auto_candidates_enabled() -> bool:
    raw = os.getenv(KNOWLEDGE_AUTO_CANDIDATES_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def sanitize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence_refs = [str(ref) for ref in candidate.get("evidence_refs", []) if ref]
    promotion = _promotion_metadata(candidate)
    return {
        "candidate_id": str(candidate.get("candidate_id") or ""),
        "status": str(candidate.get("status") or candidate.get("candidate_status") or ""),
        "candidate_type": str(candidate.get("type") or candidate.get("candidate_type") or ""),
        "confidence": candidate.get("confidence"),
        "trust_level": candidate.get("trust_level"),
        "evidence_ref": _public_evidence_ref(evidence_refs[0] if evidence_refs else candidate.get("evidence_ref")),
        "created_at": candidate.get("created_at"),
        "updated_at": candidate.get("updated_at"),
        "deduped": bool(candidate.get("deduped")),
        "promotion_decision": promotion.get("promotion_decision") or candidate.get("promotion_decision"),
        "quality_score": promotion.get("quality_score") if promotion.get("quality_score") is not None else candidate.get("quality_score"),
        "gate_summary": promotion.get("gate_summary") or _gate_summary(promotion.get("gate_results")) or candidate.get("gate_summary"),
        "review_required_reason": promotion.get("review_required_reason") or candidate.get("review_required_reason"),
    }


def sanitize_learning_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(report.get("status") or ""),
        "approval_mode": str(report.get("approval_mode") or "manual"),
        "store": str(report.get("store") or ""),
        "index_ref": report.get("index_ref"),
        "extracted_count": int(report.get("extracted_count") or 0),
        "candidate_count": int(report.get("candidate_count") or 0),
        "proposed_count": int(report.get("proposed_count") or 0),
        "promoted_count": int(report.get("promoted_count") or 0),
        "skipped_count": int(report.get("skipped_count") or 0),
        "rejected_count": int(report.get("rejected_count") or 0),
        "candidates": [sanitize_candidate(item) for item in report.get("candidates", []) if isinstance(item, dict)],
        "promoted": [sanitize_candidate(item) for item in report.get("promoted", []) if isinstance(item, dict)],
        "skipped": [sanitize_candidate(item) for item in report.get("skipped", []) if isinstance(item, dict)],
        "rejected": [sanitize_candidate(item) for item in report.get("rejected", []) if isinstance(item, dict)],
    }


def require_knowledge_admin(auth: AuthContext) -> None:
    if auth.role not in KNOWLEDGE_ADMIN_ROLES:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Knowledge approval requires owner or admin role")


class KnowledgeLearningService:
    def __init__(self, repository: ConversationRepository, artifact_store: LocalArtifactStore, *, llm_client: LLMClient | None = None) -> None:
        self.repository = repository
        self.artifact_store = artifact_store
        self.llm_client = llm_client

    @property
    def index_path(self) -> Path:
        return Path(os.getenv(KNOWLEDGE_INDEX_ENV, KNOWLEDGE_INDEX_PATH))

    @property
    def candidates_path(self) -> Path:
        return Path(os.getenv(KNOWLEDGE_CANDIDATES_ENV, KNOWLEDGE_CANDIDATES_PATH))

    @property
    def database_url(self) -> str | None:
        return os.getenv(KNOWLEDGE_DATABASE_URL_ENV)

    def list_candidates(self) -> dict[str, Any]:
        store = load_candidates(self.candidates_path, database_url=self.database_url)
        return {
            "status": "pass",
            "store": "postgres_pgvector" if self.database_url else "local_json",
            "candidates": [
                sanitize_candidate(candidate)
                for candidate in store.get("candidates", [])
                if isinstance(candidate, dict)
            ],
        }

    def propose_candidate(
        self,
        *,
        lesson: str,
        evidence_ref: str,
        candidate_type: str = "episodic",
        source_uri: str | None = None,
        confidence: str | None = None,
        trust_level: str | None = "agent_reviewed",
        metadata: dict[str, Any] | None = None,
        auth: AuthContext | None = None,
        run: AssistantRunRecord | None = None,
    ) -> dict[str, Any]:
        candidate = propose_knowledge_candidate(
            lesson,
            evidence_ref=evidence_ref,
            candidate_type=candidate_type,
            path=self.candidates_path,
            database_url=self.database_url,
            confidence=confidence,
            source_uri=source_uri,
            trust_level=trust_level,
            metadata=redact_value(metadata or {}),
        )
        sanitized = sanitize_candidate(candidate)
        if auth is not None and run is not None:
            self._append_event(auth, run.id, "knowledge.candidate.created", sanitized)
        return sanitized

    def approve_candidate(self, auth: AuthContext, candidate_id: str) -> dict[str, Any]:
        result = approve_knowledge_candidate(
            candidate_id,
            index_path=self.index_path,
            candidates_path=self.candidates_path,
            database_url=self.database_url,
        )
        candidate = self._candidate_by_id(candidate_id)
        sanitized = sanitize_candidate(candidate) if candidate else {"candidate_id": candidate_id, "status": "approved"}
        payload = {**sanitized, "result_status": result.get("status"), "store": result.get("store", "local_json")}
        self._append_candidate_event_from_evidence(auth, candidate, "knowledge.candidate.approved", payload)
        return payload

    def reject_candidate(self, auth: AuthContext, candidate_id: str) -> dict[str, Any]:
        result = reject_knowledge_candidate(candidate_id, candidates_path=self.candidates_path, database_url=self.database_url)
        candidate = self._candidate_by_id(candidate_id)
        sanitized = sanitize_candidate(candidate) if candidate else {"candidate_id": candidate_id, "status": "rejected"}
        payload = {**sanitized, "result_status": result.get("status")}
        self._append_candidate_event_from_evidence(auth, candidate, "knowledge.candidate.rejected", payload)
        return payload

    def extract_run_candidates(
        self,
        auth: AuthContext,
        run: AssistantRunRecord,
        *,
        approval_mode: str = "manual",
    ) -> dict[str, Any]:
        if approval_mode not in LEARNING_APPROVAL_MODES:
            raise ValueError(f"approval_mode must be one of {', '.join(sorted(LEARNING_APPROVAL_MODES))}")
        report = learn_knowledge_from_run(
            self.artifact_store.run_dir(run.id).parent,
            approval_mode=approval_mode,
            run_id=run.id,
            index_path=self.index_path,
            candidates_path=self.candidates_path,
            database_url=self.database_url,
            llm_judge=self._llm_judge_for_auth(auth),
        )
        sanitized = sanitize_learning_report(report)
        events = [
            ("knowledge.candidate.created", candidate)
            for candidate in sanitized["candidates"]
            if not candidate.get("deduped")
        ]
        for candidate in [*sanitized["promoted"], *sanitized["skipped"], *sanitized["rejected"]]:
            events.extend(_auto_review_events(candidate))
        events.append(
            (
                "knowledge.learning.completed",
                {
                    "status": sanitized["status"],
                    "approval_mode": sanitized["approval_mode"],
                    "candidate_count": sanitized["candidate_count"],
                    "proposed_count": sanitized["proposed_count"],
                    "promoted_count": sanitized["promoted_count"],
                    "rejected_count": sanitized["rejected_count"],
                },
            )
        )
        self._append_events(auth, run.id, events)
        return sanitized

    def maybe_extract_run_candidates(self, auth: AuthContext, run: AssistantRunRecord) -> None:
        if not knowledge_auto_candidates_enabled():
            return
        if not self._run_has_learning_artifacts(run.id):
            return
        try:
            self.extract_run_candidates(auth, run, approval_mode="guarded-auto")
        except Exception as exc:  # pragma: no cover - defensive boundary for chat/run success.
            self._append_event(
                auth,
                run.id,
                "knowledge.learning.failed",
                {"status": "failed", "error": exc.__class__.__name__, "message": "Knowledge learning extraction failed."},
            )

    def _candidate_by_id(self, candidate_id: str) -> dict[str, Any] | None:
        store = load_candidates(self.candidates_path, database_url=self.database_url)
        for candidate in store.get("candidates", []):
            if isinstance(candidate, dict) and candidate.get("candidate_id") == candidate_id:
                return candidate
        return None

    def _append_candidate_event_from_evidence(
        self,
        auth: AuthContext,
        candidate: dict[str, Any] | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        run_id = _run_id_from_candidate(candidate)
        if run_id:
            self._append_event(auth, run_id, event_type, payload)

    def _append_event(self, auth: AuthContext, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.repository.append_run_event(auth, run_id, event_type, redact_value(payload))

    def _append_events(self, auth: AuthContext, run_id: str, events: list[tuple[str, dict[str, Any]]]) -> None:
        if events:
            self.repository.append_run_events(
                auth,
                run_id,
                [(event_type, redact_value(payload)) for event_type, payload in events],
            )

    def _run_has_learning_artifacts(self, run_id: str) -> bool:
        run_dir = self.artifact_store.run_path(run_id)
        return any((run_dir / name).exists() for name in LEARNING_ARTIFACT_NAMES)

    def auto_review_candidate(self, auth: AuthContext, candidate_id: str) -> dict[str, Any]:
        reviews = review_candidates_for_auto_promotion(
            [candidate_id],
            index_path=self.index_path,
            candidates_path=self.candidates_path,
            database_url=self.database_url,
            llm_judge=self._llm_judge_for_auth(auth),
        )
        review = reviews[0]
        candidate = self._candidate_by_id(candidate_id)
        sanitized = sanitize_candidate(candidate) if candidate else {
            "candidate_id": candidate_id,
            "status": str(review.get("status") or ""),
            "promotion_decision": review.get("promotion_decision"),
            "quality_score": review.get("quality_score"),
            "gate_summary": review.get("gate_summary"),
            "review_required_reason": review.get("review_required_reason"),
        }
        for event_type, payload in _auto_review_events(sanitized):
            self._append_candidate_event_from_evidence(auth, candidate, event_type, payload)
        return sanitized

    def auto_review_candidates(self, auth: AuthContext, candidate_ids: list[str] | None = None) -> dict[str, Any]:
        reviews = review_candidates_for_auto_promotion(
            candidate_ids,
            index_path=self.index_path,
            candidates_path=self.candidates_path,
            database_url=self.database_url,
            llm_judge=self._llm_judge_for_auth(auth),
            reviewable_statuses={"needs_review", "proposed"},
        )
        reviewed_ids = {str(review.get("candidate_id") or "") for review in reviews}
        review_by_id = {str(review.get("candidate_id") or ""): review for review in reviews}
        store = load_candidates(self.candidates_path, database_url=self.database_url)
        reviewed = []
        for candidate in store.get("candidates", []):
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("candidate_id") or "")
            if candidate_id not in reviewed_ids:
                continue
            sanitized = sanitize_candidate(candidate)
            review = review_by_id.get(candidate_id)
            if review:
                sanitized.setdefault("promotion_decision", review.get("promotion_decision"))
                sanitized.setdefault("quality_score", review.get("quality_score"))
                sanitized.setdefault("gate_summary", review.get("gate_summary"))
                sanitized.setdefault("review_required_reason", review.get("review_required_reason"))
            for event_type, payload in _auto_review_events(sanitized):
                self._append_candidate_event_from_evidence(auth, candidate, event_type, payload)
            reviewed.append(sanitized)
        return {"status": "pass", "store": "postgres_pgvector" if self.database_url else "local_json", "candidates": reviewed}

    def _llm_judge_for_auth(self, auth: AuthContext):
        if self.llm_client is None:
            return None
        try:
            self.llm_client.ensure_configured()
        except Exception:
            return None

        def judge(candidate: dict[str, Any]) -> dict[str, Any]:
            try:
                chunks: list[str] = []
                for event in self.llm_client.stream(
                    messages=[
                        {"role": "system", "content": _knowledge_judge_system_prompt()},
                        {"role": "user", "content": json.dumps(_knowledge_judge_candidate_payload(candidate), sort_keys=True)},
                    ],
                    tools=[],
                    routing_context={"auth": auth, "user_tier": auth.user_tier, "stage": MODEL_STAGE_KNOWLEDGE_LEARNING_REVIEW},
                ):
                    if event.type == LLM_EVENT_MESSAGE_DELTA and event.text:
                        chunks.append(event.text)
                return _parse_knowledge_judge_response("".join(chunks))
            except Exception as exc:  # pragma: no cover - defensive provider boundary.
                return {
                    "generalizable": False,
                    "unsafe_claims": [],
                    "requires_human_review": True,
                    "reason": f"llm_judge_failed:{exc.__class__.__name__}",
                    "confidence": "low",
                }

        return judge


def _run_id_from_candidate(candidate: dict[str, Any] | None) -> str | None:
    if not isinstance(candidate, dict):
        return None
    refs = [str(ref) for ref in candidate.get("evidence_refs", []) if ref]
    source_uri = str(candidate.get("source_uri") or "")
    for value in [*refs, source_uri]:
        public_ref = _public_evidence_ref(value) or value
        match = RUN_EVIDENCE_RE.search(public_ref)
        if match:
            return match.group(1)
    return None


def _public_evidence_ref(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    if text.startswith("run:") or text.startswith("artifact:"):
        return text
    parts = Path(text).parts
    if "runs" in parts:
        index = parts.index("runs")
        if index + 1 < len(parts):
            run_id = parts[index + 1]
            artifact = "/".join(parts[index + 2 :]) or "artifact"
            if run_id.startswith(("run-", "run_")):
                return f"run:{run_id}:{artifact}"
    return Path(text).name or text[:200]


def _promotion_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    promotion = metadata.get("promotion")
    return promotion if isinstance(promotion, dict) else {}


def _gate_summary(gate_results: Any) -> list[str] | None:
    if not isinstance(gate_results, list):
        return None
    summary = []
    for gate in gate_results:
        if isinstance(gate, dict) and gate.get("name"):
            summary.append(f"{gate['name']}:{'pass' if gate.get('passed') else 'fail'}")
    return summary


def _auto_review_event_type(decision: str) -> str:
    if decision == "auto_approved":
        return "knowledge.candidate.auto_approved"
    if decision == "auto_rejected":
        return "knowledge.candidate.auto_rejected"
    return "knowledge.candidate.needs_review"


def _auto_review_events(candidate: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    decision = str(candidate.get("promotion_decision") or "")
    if decision not in {"auto_approved", "auto_rejected", "needs_review"}:
        return []
    return [
        ("knowledge.candidate.auto_reviewed", candidate),
        (_auto_review_event_type(decision), candidate),
    ]


def _knowledge_judge_system_prompt() -> str:
    return (
        "You review proposed knowledge updates for a trading strategy assistant. "
        "Return only JSON with keys generalizable:boolean, unsafe_claims:string[], "
        "requires_human_review:boolean, reason:string, confidence:number. "
        "Approve only reusable process, validation, repair, or evidence-handling lessons. "
        "Require human review for trading edge, market interpretation, profit, performance, "
        "broker, live trading, no-loss, certification, secret, prompt, provider route, or raw diagnostic claims."
    )


def _knowledge_judge_candidate_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    metadata = candidate.get("metadata") if isinstance(candidate.get("metadata"), dict) else {}
    learning = metadata.get("learning") if isinstance(metadata.get("learning"), dict) else {}
    return {
        "lesson": str(candidate.get("lesson") or "")[:2000],
        "lesson_kind": candidate.get("lesson_kind"),
        "candidate_type": candidate.get("type") or candidate.get("candidate_type"),
        "confidence": candidate.get("confidence"),
        "trust_level": candidate.get("trust_level"),
        "domain_tags": candidate.get("domain_tags") if isinstance(candidate.get("domain_tags"), list) else [],
        "platform_tags": candidate.get("platform_tags") if isinstance(candidate.get("platform_tags"), list) else [],
        "stages": candidate.get("stages") if isinstance(candidate.get("stages"), list) else [],
        "evidence_count": learning.get("evidence_count") or len(candidate.get("evidence_refs") or []),
    }


def _parse_knowledge_judge_response(text: str) -> dict[str, Any]:
    parsed = extract_json_object(text)
    if parsed is None:
        return _invalid_knowledge_judge_payload("invalid_json")
    return {
        "generalizable": bool(parsed.get("generalizable")),
        "unsafe_claims": [str(item) for item in parsed.get("unsafe_claims", []) if item] if isinstance(parsed.get("unsafe_claims"), list) else [],
        "requires_human_review": bool(parsed.get("requires_human_review")),
        "reason": str(parsed.get("reason") or ""),
        "confidence": parsed.get("confidence") if isinstance(parsed.get("confidence"), (int, float)) else str(parsed.get("confidence") or "low"),
    }


def _invalid_knowledge_judge_payload(reason: str) -> dict[str, Any]:
    return {
        "generalizable": False,
        "unsafe_claims": [],
        "requires_human_review": True,
        "reason": reason,
        "confidence": "low",
    }
