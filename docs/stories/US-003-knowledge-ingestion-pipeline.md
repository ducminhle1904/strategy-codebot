# US-003 - Knowledge Ingestion Pipeline

## Status

Planned

## Goal

Implement a source-registry-driven ingestion pipeline for official Pine, MQL5, MetaTrader, and internal knowledge docs.

## Acceptance Criteria

- Reads `configs/source-registry.yaml`.
- Stores source metadata with freshness and trust level.
- Supports official and internal sources.
- Produces update proposals instead of silently changing canonical docs.

## Verify Command

```bash
uv run pytest tests/test_knowledge.py
```

## Phase

Phase 1
