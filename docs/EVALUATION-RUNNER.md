# Executable evaluation runner

The checkpoint-2 runner executes evaluation against the real mikuRAG pipeline.
It does not accept pre-authored retrieval observations as proof of a run.

## Lifecycle

For every invocation, the runner:

1. loads and validates a versioned manifest under `backend/evaluation/corpus/`;
2. creates a uniquely named, isolated evaluation Knowledge Base;
3. copies every corpus source into managed storage and creates Pending Documents;
4. sends the normal `mikurag.documents.ingest` tasks to Redis/Celery;
5. polls PostgreSQL until every Document is Ready or Failed;
6. embeds each query and calls the production hybrid retriever;
7. optionally calls the production grounded generation and validation path;
8. writes a raw run record, JSON aggregate, and Markdown report; and
9. deletes the isolated Knowledge Base, chunks, and stored source files by default.

Provider keys are never written to evaluation artifacts. Reports record public
configuration identifiers such as model IDs and chunking version.

## Run

Start PostgreSQL, Redis, and the worker, then run:

```powershell
.\scripts\mikurag.ps1 evaluate
```

```sh
sh ./scripts/mikurag.sh evaluate
```

To include grounded answers:

```powershell
docker compose --profile tools run --rm evaluate python -m app.evaluation_cli --answers
```

Use `--keep-knowledge-base` only for manual debugging. The default cleanup makes
repeat runs independent and prevents evaluation Documents from entering normal
application retrieval.

## Artifacts

Each run writes to
`backend/evaluation/results/<evaluation-set-version>/<run-id>/`:

- `raw-run.json`: immutable run metadata, durable Document states, per-case
  retrieved evidence and locators, timings, optional answers, and safe failures;
- `report.json`: aggregate metrics and per-category metrics; and
- `report.md`: a compact human-readable summary.

Failed ingestion or provider runs still write the available raw state and report
before returning a non-zero exit code.

## Corpus schema

`manifest.json` schema version 1 contains redistribution metadata, Documents,
and cases. The corpus must declare its license/provenance, reference a non-empty
license file, and explicitly declare that it contains no sensitive data. Each
Document has a stable lowercase `document_id`, `passage_id`, `locator_id`, and a
corpus-relative source path. Cases identify relevant and required passage IDs.
The runner copies the passage and locator IDs into the non-secret chunk
provenance allowlist as `source_passage_id` and `source_locator_id`, so raw run
records can address the exact gold passage and source locator independently of
database UUIDs, filenames, or hand-authored observations.
