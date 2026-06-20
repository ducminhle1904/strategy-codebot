# 0025 Trading Knowledge Base and Retrieval

## Status

Accepted.

## Decision

Strategy Codebot will use a Trading Knowledge Base for live knowledge context instead of relying only on static curated markdown snippets.

The production storage target is Postgres with pgvector and PostgreSQL full-text search. The repository also includes a local JSON adapter for deterministic development, tests, and offline runs. The local adapter mirrors the production metadata shape and stores the Postgres schema reference so generated artifacts remain migration-ready.

The production Postgres adapter is enabled with `STRATEGY_CODEBOT_KNOWLEDGE_DATABASE_URL` or the `knowledge --db-url` command option. When a database URL is present, `knowledge init`, `knowledge ingest`, `knowledge search`, `knowledge eval`, and candidate approval commands read and write the Postgres store. When no database URL is present, commands continue to use the local JSON adapter.

V1 supports three embedding profiles:

- `local`: deterministic 64-dimension hash embeddings for tests, offline development, and deterministic fixtures.
- `production-openrouter`: OpenRouter embeddings API with `openai/text-embedding-3-small`, 1536 dimensions, and `OPENROUTER_API_KEY`.
- `production-openai`: OpenAI embeddings API with `text-embedding-3-small`, 1536 dimensions, and `OPENAI_API_KEY`.

Production databases should be initialized with `strategy-codebot knowledge init --embedding-profile production-openrouter --db-url ...` or the OpenAI equivalent before live retrieval uses the store. Changing an existing database from the local profile to a production profile requires reinitializing or migrating the pgvector column because vector dimensions are fixed by schema.

Production retrieval caches query embeddings in Postgres table `knowledge_query_embeddings`, keyed by normalized query, stage, embedding provider/model, and knowledge version. The cache stores only query vectors, not final retrieved chunks, so ranking still reflects current indexed knowledge. `strategy-codebot knowledge health --db-url ...` and API `/ready` expose read-only KB readiness without leaking credentials.

## Consequences

- `--knowledge-context auto` uses the KB retrieval path when `knowledge/kb/index.json` exists or `STRATEGY_CODEBOT_KNOWLEDGE_INDEX` points to an index.
- If no KB index exists, live runs keep the previous static curated-doc behavior.
- Retrieval uses deterministic hybrid scoring: exact/full-text style ranking, vector-style ranking, RRF merge, and lightweight reranking.
- Knowledge updates are candidate-based and require approval before becoming retrievable.
- External web sources are not fetched during live generation.
