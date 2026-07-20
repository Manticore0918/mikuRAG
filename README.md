# mikuRAG

mikuRAG is a self-hosted, multi-user private knowledge system for grounded answers with citations.

Phases 1 through 4 establish the application foundation, authorization boundary, Document Ingestion pipeline, hybrid retrieval, and grounded chat: FastAPI, React, PostgreSQL with pgvector, Redis-backed Celery, secure sessions, Knowledge Base access grants, secure upload storage, text extraction, chunking, embeddings, lexical/vector rank fusion, validated answers, Citations, and Conversation history.

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

## Documentation

- Product language: [`CONTEXT.md`](./CONTEXT.md)
- Approved MVP plan: [`docs/MVP-PLAN.md`](./docs/MVP-PLAN.md)
- Architectural decisions: [`docs/adr`](./docs/adr)
