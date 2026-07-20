# mikuRAG MVP Plan

## Outcome

Deliver a self-hosted, multi-user private knowledge system in which Administrators curate access-controlled Knowledge Bases and Users ask grounded questions against one Knowledge Base at a time.

The MVP is complete when an Administrator can configure providers, create Users and Knowledge Bases, grant access, ingest supported Documents, and audit Conversations; an authorized User can receive a streamed Grounded Answer with inspectable Citations; and unauthorized or unsupported content never enters retrieval.

## Product contract

- An Installation contains Administrator-created username/password accounts.
- Administrators control all Users, Knowledge Bases, Documents, permissions, settings, and Conversations.
- Users can discover and query only explicitly assigned Knowledge Bases.
- One Conversation is permanently scoped to one Knowledge Base.
- Every factual answer must be grounded in freshly retrieved Document evidence and include Citations.
- Missing or conflicting evidence produces an explicit inability to answer reliably.
- Previous answers provide conversational context but never count as evidence.
- Users are told that Administrators can inspect and delete their Conversations.

## MVP interface

### User area

- Login and Administrator-visibility disclosure.
- List of authorized Knowledge Bases.
- Conversation list and deletion of owned Conversations.
- Streaming chat with expandable Citation excerpts and links to allowed source locations.
- Clear states for missing evidence, provider failure, and interrupted generation.

### Administrator area

- Create, disable, and reset passwords for Users.
- Create and delete Knowledge Bases.
- Grant or revoke User access to each Knowledge Base.
- Upload, list, retry, and delete Documents; inspect Ingestion status and safe failure details.
- Configure and test the embedding API and Ollama endpoint.
- Inspect and delete all Conversations.
- View health status for PostgreSQL, Redis, the embedding provider, and Ollama.

## Document and Ingestion scope

- Supported: text-extractable PDF, DOCX, TXT, and Markdown.
- Rejected: scanned/image-only documents, unsupported formats, files over 50 MB, and documents over 500 pages.
- Uploads use durable, sequential 5 MiB parts. A server-confirmed checkpoint survives network loss, page reload, and API restart for 24 hours after the most recent activity.
- Incomplete uploads are visible to Administrators but are not Documents. Finalization verifies size, SHA-256, and format before atomically creating a Pending Document.
- Deferred: OCR, images, diagrams, charts, video, document editing, and visual question answering.
- Visible lifecycle: Pending, Processing, Ready, or Failed.
- Only Ready Documents participate in retrieval; partial Ingestion is never visible.
- Celery workers receive durable jobs through Redis. PostgreSQL is the canonical source of job and Document state.
- Deleting a Document immediately excludes it from new retrieval, then removes its original, chunks, and vectors in the background.
- Historical answers and small Citation excerpts remain until their Conversations are deleted; deleted originals cannot be reopened.

## Retrieval and answer flow

1. Authorize the User against the Conversation's single Knowledge Base.
2. Use recent Conversation history locally to rewrite a follow-up into a standalone query.
3. Send that query to the configured remote embedding API.
4. Run authorization-scoped semantic search with pgvector and lexical search with PostgreSQL full-text search.
5. Fuse the two rankings and select a bounded evidence set; no reranking model is used in the MVP.
6. Send the question, recent context, and retrieved evidence to the local Ollama endpoint.
7. Stream progress while buffering the model's structured answer, validate every Citation identifier against the supplied evidence, then release only the validated answer text over SSE.
8. Persist the question, final answer or failure state, model metadata, and Citation excerpts.

Each turn performs fresh retrieval. Conversation history may resolve references but cannot support factual claims by itself.

## Model and privacy boundary

- Embeddings: Alibaba Cloud Model Studio, pinned to `tongyi-embedding-vision-flash-2026-03-06` and authenticated with an Administrator-provided API key.
- Generation: `DeepSeek-R1-Distill-Qwen-7B` through an Administrator-configured OpenAI-compatible Ollama endpoint.
- Document chunks and rewritten retrieval queries leave the Installation for embedding. Retrieved evidence and answer generation remain within the configured Ollama boundary.
- The UI discloses the remote embedding boundary before the provider is enabled.
- Provider credentials are encrypted in PostgreSQL with a master key supplied to the backend by environment variable. Secrets are never returned, logged, or placed in Redis job payloads.
- Changing the embedding model requires a complete re-Ingestion because vectors from different models are not mixed.

## Technical architecture

- Frontend: React, TypeScript, and Vite.
- API: Python 3.12, FastAPI, SQLAlchemy, and Alembic.
- Worker: Celery with Redis as broker.
- Data: PostgreSQL with pgvector.
- Original Documents: persistent filesystem volume with opaque server-generated paths.
- Incomplete upload bytes: opaque temporary paths on the same persistent volume; PostgreSQL is authoritative for confirmed offsets and expiry.
- Model generation: external Ollama service through an OpenAI-compatible HTTP endpoint.
- API style: REST for management and Server-Sent Events for streamed answer events.
- Packaging: Docker Compose services for frontend, API, worker, PostgreSQL/pgvector, and Redis. Ollama remains external.

The API is the sole authorization boundary. The frontend, worker payloads, document identifiers, and model prompts are never trusted to enforce access.

## Core records

- User: identity, password hash, Administrator flag, enabled state, timestamps.
- Knowledge Base: name, description, lifecycle timestamps.
- Knowledge Base Access: User-to-Knowledge-Base grant.
- Document: Knowledge Base, original name, storage identity, checksum, media type, size, page count, Ingestion state, safe error, timestamps.
- Upload Session: Knowledge Base, initiator, safe original name, expected size/checksum, confirmed offset, opaque temporary/final identities, expiry, status, and resulting Document.
- Upload Part Receipt: Upload Session, byte offset, length, and checksum for idempotent retry handling.
- Chunk: Document, ordered text, page/section locator, lexical-search data, embedding, embedding version.
- Conversation: owner, immutable Knowledge Base, title, timestamps.
- Message: Conversation, role, content, completion/failure state, model metadata, timestamps.
- Citation: answer Message, Document identity/name, locator, retained excerpt, retrieval rank/score.
- Provider Configuration: endpoints, model identifiers, encrypted secret, validation state.

All retrieval queries include the authorized Knowledge Base identifier before ranking. Disabling a User or revoking a grant takes effect on the next request.

## Capacity envelope

- 100 accounts and 20 Knowledge Bases per Installation.
- 10,000 Documents or 100,000 text pages total.
- 20 active Users; generation concurrency is bounded by the configured Ollama capacity.
- Ingestion can queue without blocking chat retrieval.
- At most 20 incomplete Upload Sessions are retained at once, bounding temporary upload data to approximately 1 GB under the 50 MB Document limit.
- Large-scale horizontal scaling and high availability are outside the MVP.

## Security baseline

- Argon2id password hashing and secure, HTTP-only, same-site session cookies.
- CSRF protection for cookie-authenticated mutations, login throttling, and upload size/type validation.
- Authorization checks on every Knowledge Base, Document, Conversation, Citation, and streaming endpoint.
- No original filename is used as a filesystem path.
- Upload Parts are accepted only at the server-confirmed offset and are checked against their declared length and SHA-256 before the checkpoint advances.
- Provider errors and worker failures expose safe messages to the UI and retain detailed server-side logs without secrets or Document content.
- HTTPS is required outside localhost and terminated by an operator-managed reverse proxy.

## Delivery sequence

### 1. Foundation

Create the service layout, Docker Compose environment, PostgreSQL/pgvector schema and migrations, Redis/Celery wiring, configuration validation, health checks, and test harness.

### 2. Identity and authorization

Implement first-Administrator bootstrap, login/logout, password management, User disabling, Knowledge Base CRUD, access grants, and authorization tests that exercise cross-Knowledge-Base denial.

### 3. Document Ingestion

Implement secure upload storage, format validation and text extraction, structure-aware chunking, embedding calls, atomic Ready/Failed transitions, retries, deletion, and ingestion status UI.

### 4. Retrieval and grounded chat

Implement hybrid retrieval, follow-up rewriting, evidence packaging, Ollama streaming, strict grounded-answer prompting, Citation validation/persistence, insufficient-evidence behavior, and chat UI.

### 5. Administration and privacy

Implement provider settings and connection tests, Conversation audit/deletion, disclosure screens, document-deletion warnings, and operational health views.

### 6. Hardening and handoff

Add end-to-end authorization and RAG fixtures, failure/retry tests, capacity smoke tests, backup/restore documentation, deployment instructions, and an MVP acceptance runbook.

## Acceptance gates

- A User cannot list, retrieve, cite, or infer content from an unassigned Knowledge Base, including through guessed identifiers or streaming endpoints.
- Failed or partially processed Documents never appear in retrieval.
- Every factual answer contains valid Citations to evidence supplied for that turn, or explicitly refuses for insufficient/conflicting evidence.
- Follow-up questions resolve against recent context but re-retrieve evidence.
- Revoking access or disabling a User blocks the next request.
- Document deletion removes new retrieval access immediately and eventually purges stored originals, chunks, and vectors.
- API keys and encryption keys do not appear in responses, logs, Redis payloads, or exported diagnostics.
- The documented backup can restore accounts, permissions, Knowledge Bases, Documents, vectors, and Conversations on a clean Installation.
- The system remains usable for chat retrieval while Ingestion jobs are queued within the agreed capacity envelope.
- An interrupted upload resumes from the server-confirmed checkpoint after the same file is reselected; expired, cancelled, corrupt, and orphaned temporary bytes are removed.

## Explicit non-goals

Public registration, email workflows, SSO, social login, cross-Knowledge-Base chat, User uploads, OCR, visual understanding, audio/video, web crawling, connectors, document editing, sharing, exports, analytics, mobile apps, Kubernetes, automated cloud provisioning, and high availability.
