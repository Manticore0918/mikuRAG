# mikuRAG

mikuRAG is a self-hosted, multi-user private knowledge system for grounded answers with citations.

The stable default path uses legacy character chunking, pgvector semantic search,
PostgreSQL full-text search, reciprocal-rank fusion, validated Grounded Answers,
and server-owned Citations. Hierarchical chunking, parent/child expansion,
generated summaries, rollout jobs, evaluation gates, and their observability are
experimental and feature-off by default. They can be exercised explicitly
without changing the stable retrieval path.

The current checkpoint-0 work consolidates the existing application foundation,
authorization boundary, asynchronous Document Ingestion, grounded chat, and the
feature-flagged hierarchical implementation into a reproducible baseline. It is
not described as the default until the committed acceptance report passes.

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

Administrators can upload, inspect, retry, and delete text-extractable PDF, DOCX, TXT, and Markdown Documents. Files are limited to 50 MB and PDFs to 500 pages. Scanned or image-only files fail safely because OCR is outside this MVP.

Uploads are split into sequential 5 MiB parts. PostgreSQL records the confirmed byte offset and the persistent upload volume retains incomplete bytes, so a transfer can resume after network loss, page reload, or API restart. The Administrator reselects the same file after a reload; mikuRAG verifies its SHA-256 before continuing. Open Upload Sessions expire after 24 hours without activity, and the scheduled `beat` service removes expired or orphaned temporary data hourly.

A source becomes a Document only after all bytes arrive and the server independently verifies its total size, SHA-256, and format. At most 20 Upload Sessions can remain open across the Installation, while the existing 50 MB and 500-page limits remain unchanged.

PostgreSQL is authoritative for `pending`, `processing`, `ready`, `failed`, and `deleting` states. Chunks and vectors are committed only when a Document becomes `ready`. Deletion changes the state to `deleting` before background removal, allowing retrieval to exclude it immediately.

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

## Reproducible baseline demo

The versioned demo contains a two-page PDF, a Markdown release guide, and six
questions covering exact identifiers, paraphrase, follow-up rewriting,
Citations, insufficient evidence, and an authorization boundary. The seed is
idempotent and sends both Documents through the real worker.

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
- Checkpoint-0 demo and proof script: [`docs/BASELINE-DEMO.md`](./docs/BASELINE-DEMO.md)
- Approved MVP plan: [`docs/MVP-PLAN.md`](./docs/MVP-PLAN.md)
- Hierarchical chunking rollout and rollback: [`docs/CHUNKING-CONFIG.md`](./docs/CHUNKING-CONFIG.md)
- Hierarchical chunking observation events: [`docs/CHUNKING-OBSERVABILITY.md`](./docs/CHUNKING-OBSERVABILITY.md)
- Chunking performance and capacity profiles: [`docs/CHUNKING-PERFORMANCE.md`](./docs/CHUNKING-PERFORMANCE.md)
- Hierarchical chunking rollout operations: [`docs/CHUNKING-ROLLOUT.md`](./docs/CHUNKING-ROLLOUT.md)
- Default-rollout acceptance gates: [`docs/CHUNKING-ACCEPTANCE.md`](./docs/CHUNKING-ACCEPTANCE.md)
- Hierarchical chunking risk controls: [`docs/CHUNKING-RISKS.md`](./docs/CHUNKING-RISKS.md)
- Architectural decisions: [`docs/adr`](./docs/adr)
