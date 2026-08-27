# mikuRAG

mikuRAG is a self-hosted, multi-user private knowledge system for grounded answers with citations.

The stable default path uses legacy character chunking, pgvector semantic search,
PostgreSQL full-text search, reciprocal-rank fusion, validated Grounded Answers,
and server-owned Citations. Hierarchical chunking, parent/child expansion,
generated summaries, rollout jobs, evaluation gates, and their observability are
experimental and feature-off by default. They can be exercised explicitly
without changing the stable retrieval path.

Checkpoint 1 extends the reproducible baseline with heterogeneous, asynchronous
Ingestion and source-specific provenance. PDF, HTML, Markdown, Python, and
TypeScript can coexist in one Knowledge Base while the feature-flagged
hierarchical implementation remains experimental.

## Local container startup

1. Copy `.env.example` to `.env` and replace every placeholder secret.
2. Run the database migration explicitly:

   ```powershell
   docker compose --profile tools run --rm migrate
   ```

3. Start the foundation:

   ```powershell
   docker compose up --build
   ```

4. Create the first Administrator in a separate terminal:

   ```powershell
   docker compose run --rm backend python -m app.bootstrap_admin --username admin
   ```

   The command prompts for a password of at least 12 characters and refuses to run after an Administrator exists.

5. Open `http://localhost:5173` and sign in. Administrators can provision Users, create Knowledge Bases, and manage access grants.

Windows and POSIX entry points wrap the repeatable workflows:

```powershell
.\scripts\mikurag.ps1 setup
.\scripts\mikurag.ps1 checks
.\scripts\mikurag.ps1 migrations
```

```sh
sh ./scripts/mikurag.sh setup
sh ./scripts/mikurag.sh checks
sh ./scripts/mikurag.sh migrations
```

Ollama remains external to Compose. Set `MIKURAG_GENERATION_BASE_URL` to its OpenAI-compatible `/v1` endpoint and set `MIKURAG_GENERATION_MODEL_ID` to the installed model tag. The approved default is `DeepSeek-R1-Distill-Qwen-7B`; if Ollama exposes it under a tag such as `deepseek-r1:7b`, use that exact tag in the environment.

## Document Ingestion

Set `MIKURAG_EMBEDDING_API_KEY` before ingesting Documents. The worker calls the configured Alibaba Model Studio endpoint with `tongyi-embedding-vision-flash-2026-03-06`; extracted chunks leave the Installation for embedding. The key is read from the API/worker environment and is never placed in a Redis task payload.

Administrators can upload, inspect, retry, and delete text-extractable PDF, DOCX,
TXT, Markdown, HTML, Python, JavaScript, and TypeScript Documents. Files are
limited to 50 MB and PDFs to 500 pages. Text source files must be valid UTF-8.
Scanned or image-only PDFs, malformed source files, and unsafe inputs fail with a
bounded user-facing error because OCR is outside this MVP.

Uploads are split into sequential 5 MiB parts. PostgreSQL records the confirmed byte offset and the persistent upload volume retains incomplete bytes, so a transfer can resume after network loss, page reload, or API restart. The Administrator reselects the same file after a reload; mikuRAG verifies its SHA-256 before continuing. Open Upload Sessions expire after 24 hours without activity, and the scheduled `beat` service removes expired or orphaned temporary data hourly.

A source becomes a Document only after all bytes arrive and the server independently verifies its total size, SHA-256, and format. At most 20 Upload Sessions can remain open across the Installation, while the existing 50 MB and 500-page limits remain unchanged.

PostgreSQL is authoritative for `pending`, `processing`, `ready`, `failed`, and `deleting` states. Chunks and vectors are committed only when a Document becomes `ready`. Deletion changes the state to `deleting` before background removal, allowing retrieval to exclude it immediately.

The Administrator view exposes the durable Ingestion stage, percentage,
attempts, parser/chunker versions, and safe warnings. Document provenance stores
source kind, language, tags, URI or repository-relative path, and validated JSON
metadata. Only a small retrieval-relevant allowlist is copied to chunk locators;
secret-like metadata keys are rejected.

### Provenance and Citation proof

| Source | Extracted structure | Stored locator | Rendered Citation |
| --- | --- | --- | --- |
| PDF | pages and layout blocks | `page`, `start_page`, `end_page` | `p. 14` or `pp. 14–15` |
| HTML | title, heading path, content element, text offsets | `heading_path`, `element`, `line_start/end`, `text_start/end`, source URI | heading · element · lines |
| Markdown | heading hierarchy and source lines | `heading_path`, `line_start/end` | heading · lines |
| Python | module and AST-discovered symbols | repository-relative `path`, `symbol`, `line_start/end`, `language` | `path/to/file.py:40–55 · symbol()` |
| TypeScript/JavaScript | top-level declarations with a line-aware fallback | repository-relative `path`, `symbol`, `line_start/end`, `language` | `src/client.ts:12–18 · symbol` |

## Grounded chat

Each Conversation is permanently scoped to one Knowledge Base. Every turn rechecks access, rewrites follow-up references using recent history, embeds the standalone query, and performs both pgvector semantic search and PostgreSQL full-text search over Ready Documents in that Knowledge Base. Reciprocal-rank fusion selects a bounded evidence set.

The local model returns structured claims linked to Evidence identifiers. mikuRAG buffers and validates the complete model response, creates Citation markers itself, persists retained excerpts, and only then sends answer text to the browser. Missing evidence, conflicting evidence, unknown Citation identifiers, and unverifiable output produce an explicit inability to answer reliably rather than unsupported factual text.

Conversation endpoints use Server-Sent Events for progress and validated answer delivery. The reverse proxy disables buffering and allows up to ten minutes for slower local generation.

## Phase 2 security behavior

- Passwords use Argon2id hashing.
- Browser sessions are signed, HTTP-only, same-site cookies and are checked against current User state on every request.
- Disabling a User, changing their Administrator role, or resetting their password invalidates existing sessions.
- Browser mutations require a matching CSRF cookie and header.
- Failed login attempts are throttled through Redis without revealing whether a username exists.
- Non-Administrators receive `404 Not Found` for Knowledge Bases they are not assigned, avoiding resource disclosure.

## Development checks

Backend checks run from `backend` after installing the `dev` extra:

```powershell
python -m pytest
python -m ruff check --no-cache .
```

Frontend checks run from `frontend`:

```powershell
npm test
npm run lint
npm run build
```

## Executable evaluation

The reviewed `gold_v1` corpus (64 questions across train/dev/test splits with
graded qrels) is ingested into a unique, isolated Knowledge Base and queried
through the production embedding and hybrid retrieval path:

```powershell
.\scripts\mikurag.ps1 evaluate
```

The runner waits for real Celery Ingestion, writes `raw-run.json`, `report.json`,
and `report.md` under `backend/evaluation/results/`, then deletes its Knowledge
Base and managed source files. The evaluation CLI exposes three subcommands:

- `run` — execute one configuration (`--chunking-version`, `--retrieval-mode`,
  `--reranker`, `--answers`, `--max-cases` for a CI smoke subset).
- `compare` — run every chunking profile against the same corpus and produce
  Recall/MRR/NDCG, latency, token, and storage results with a per-candidate
  acceptance gate on the untouched test split.
- `ablation` — run the retrieval experiment modes (`vector`, `fts_baseline`,
  `bm25`, `hybrid_rrf`, `hybrid_rrf_reranked`) against the frozen split and
  publish a Recall@10/MRR@10/NDCG@10/p95/evidence-token table to
  `backend/evaluation/results/ablation/<version>/<split>/`.

See [`docs/EVALUATION-RUNNER.md`](./docs/EVALUATION-RUNNER.md) for the lifecycle,
retrieval modes, corpus schema, failure behavior, and artifact contract.

## Reproducible baseline demo

The versioned demo contains a two-page PDF plus Markdown, HTML, Python, and
TypeScript sources. Nine questions cover exact identifiers, paraphrase,
follow-up rewriting, page/heading/element/path locators, insufficient evidence,
and an authorization boundary. The idempotent seed sends all five Documents
through the real worker; legacy demo rows are re-ingested once to add parser and
chunker version provenance.

Set two temporary passwords in your shell, then run the seed, structural smoke,
and restart-durability checks:

```powershell
$env:MIKURAG_DEMO_ADMIN_PASSWORD = "replace-with-a-demo-admin-password"
$env:MIKURAG_DEMO_USER_PASSWORD = "replace-with-a-demo-user-password"
.\scripts\mikurag.ps1 seed
.\scripts\mikurag.ps1 smoke
.\scripts\mikurag.ps1 restart-smoke
```

```sh
export MIKURAG_DEMO_ADMIN_PASSWORD='replace-with-a-demo-admin-password'
export MIKURAG_DEMO_USER_PASSWORD='replace-with-a-demo-user-password'
sh ./scripts/mikurag.sh seed
sh ./scripts/mikurag.sh smoke
sh ./scripts/mikurag.sh restart-smoke
```

The worker still uses the configured embedding provider, and the interactive
questions use the configured generation provider. See
[`docs/BASELINE-DEMO.md`](./docs/BASELINE-DEMO.md) for the exact proof script and
expected evidence.

![Checkpoint-0 grounded answer with an expanded page-1 Citation](./docs/assets/checkpoint-0-baseline.png)

## Documentation

- Product language: [`CONTEXT.md`](./CONTEXT.md)
- Checkpoint-1 multi-source demo and proof script: [`docs/BASELINE-DEMO.md`](./docs/BASELINE-DEMO.md)
- Executable evaluation runner: [`docs/EVALUATION-RUNNER.md`](./docs/EVALUATION-RUNNER.md)
- Approved MVP plan: [`docs/MVP-PLAN.md`](./docs/MVP-PLAN.md)
- Hierarchical chunking rollout and rollback: [`docs/CHUNKING-CONFIG.md`](./docs/CHUNKING-CONFIG.md)
- Hierarchical chunking observation events: [`docs/CHUNKING-OBSERVABILITY.md`](./docs/CHUNKING-OBSERVABILITY.md)
- Chunking performance and capacity profiles: [`docs/CHUNKING-PERFORMANCE.md`](./docs/CHUNKING-PERFORMANCE.md)
- Hierarchical chunking rollout operations: [`docs/CHUNKING-ROLLOUT.md`](./docs/CHUNKING-ROLLOUT.md)
- Default-rollout acceptance gates: [`docs/CHUNKING-ACCEPTANCE.md`](./docs/CHUNKING-ACCEPTANCE.md)
- Hierarchical chunking risk controls: [`docs/CHUNKING-RISKS.md`](./docs/CHUNKING-RISKS.md)
- Architectural decisions: [`docs/adr`](./docs/adr)
