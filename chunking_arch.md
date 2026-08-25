# Large-Document Chunking Architecture Plan

## 1. Goal

Improve retrieval quality for large documents, especially PDFs with hundreds of
pages, without unnecessarily increasing prompt size, embedding cost, or latency.

The completed architecture should:

- Preserve ideas that cross page boundaries.
- Split on document structure and semantic boundaries instead of arbitrary
  character positions.
- Use small chunks for precise search and larger parent sections for context.
- Preserve accurate page and section citations.
- Expand neighboring context only when it is useful.
- Support broad document questions through hierarchical summaries.
- Allow existing documents to be reprocessed safely when the chunking version
  changes.

## 2. Current State

The current ingestion pipeline:

1. Extracts each PDF page into an independent `ExtractedSection`.
2. Splits each section into approximately 800-character chunks.
3. Adds approximately 100 characters of overlap within the same page.
4. Stores a page locator and a document-wide chunk ordinal.
5. Embeds every chunk and creates a PostgreSQL full-text search vector.

The current retrieval pipeline:

1. Selects up to 20 semantic candidates.
2. Selects up to 20 lexical candidates.
3. Combines them using reciprocal rank fusion.
4. Returns up to 8 evidence chunks.
5. Limits evidence to 3 chunks from any one document.

### Current limitations

- Pages are hard chunking boundaries.
- Chunk sizes are measured in characters rather than model tokens.
- Headings, paragraphs, lists, and tables are not represented explicitly.
- Overlap can duplicate text without guaranteeing useful context.
- Matching adjacent chunks are returned independently instead of being merged.
- There is no parent-child relationship between precise search chunks and
  larger context sections.
- A three-chunk-per-document limit is restrictive for a large single-document
  knowledge base.
- Ordinary top-k retrieval cannot reliably summarize or exhaustively inspect a
  large document.

## 3. Target Architecture

```text
File
  -> structured extraction
  -> normalization
  -> ordered document blocks
  -> parent sections
  -> searchable child chunks
  -> embeddings and lexical index

Question
  -> query classification
  -> hybrid child-chunk retrieval
  -> reciprocal-rank fusion
  -> reranking
  -> neighbor or parent expansion
  -> adjacent-range merging
  -> token-budgeted evidence assembly
  -> answer with citations
```

The architecture will use three content representations:

- **Block:** the smallest extracted structural element, such as a heading,
  paragraph, list item, or table.
- **Child chunk:** a small, searchable unit used for embeddings and lexical
  retrieval.
- **Parent section:** a larger coherent unit used to provide surrounding
  context after a child chunk matches.

Document and section summaries will form a separate hierarchy for broad
questions.

## 4. Design Decisions

### 4.1 Page numbers are metadata, not text boundaries

Ordered blocks may be grouped across page boundaries. Every block and chunk
must retain its source page range so citations remain accurate.

### 4.2 Token-aware sizing

Use the tokenizer appropriate for the embedding model when available. Otherwise,
use a documented conservative tokenizer or approximation behind a common
interface.

Initial tuning values:

| Setting | Initial value |
| --- | ---: |
| Child target | 500 tokens |
| Child minimum | 200 tokens |
| Child maximum | 750 tokens |
| Child overlap | 60 tokens |
| Parent target | 2,000 tokens |
| Parent maximum | 3,000 tokens |

These values are starting points and must be tuned with retrieval evaluation,
not treated as permanent constants.

### 4.3 Structure-aware split priority

When forming chunks, prefer boundaries in this order:

1. Section or heading boundary
2. Paragraph boundary
3. List-item or table boundary
4. Sentence boundary
5. Token-limit hard split

### 4.4 Parent-child retrieval

Only child chunks are required in the primary semantic and lexical indexes.
After a child matches, retrieval may add:

- The matching child alone for a narrow fact.
- Its previous and next child chunks.
- The relevant part of its parent section.

Expansion must be governed by a total token budget rather than a fixed number
of chunks.

### 4.5 Specialized block handling

- Keep small tables intact.
- Split large tables by row groups and repeat the column header in each child.
- Keep a heading with at least the first paragraph beneath it.
- Avoid separating a list introduction from its list items.
- Mark code blocks and preformatted content so sentence splitting is not used.
- Remove repeated headers and footers before chunk construction.

## 5. Data Model Changes

### 5.1 Extend `chunks`

Add:

- `parent_chunk_id`: nullable self-reference for child-to-parent association.
- `chunk_level`: `child`, `parent`, `section_summary`, or `document_summary`.
- `start_page`: nullable integer.
- `end_page`: nullable integer.
- `start_offset`: nullable document-level normalized-text offset.
- `end_offset`: nullable document-level normalized-text offset.
- `heading_path`: JSON array containing the section hierarchy.
- `content_type`: paragraph, list, table, code, mixed, or summary.
- `token_count`: integer.
- `chunking_version`: string.
- `content_hash`: hash of normalized chunk content and relevant metadata.

Retain:

- `ordinal` as the stable order within a document and chunk level.
- `locator` for backward-compatible citation metadata during migration.

Consider changing the uniqueness constraint from `(document_id, ordinal)` to
`(document_id, chunk_level, ordinal)`.

### 5.2 Parent storage decision

Store parents in the existing `chunks` table so retrieval, citations, and
document deletion continue to use the same ownership model. Only child chunks
need embeddings initially. Parent embeddings can be added later if evaluation
shows a benefit.

### 5.3 Neighbor lookup

Use `(document_id, chunk_level, ordinal)` for neighbor lookup rather than storing
explicit previous and next IDs. Add a supporting composite index.

### 5.4 Migration

Create an Alembic migration that:

1. Adds nullable columns and new indexes.
2. Backfills existing chunks as `chunk_level = 'child'`.
3. Copies page data from `locator` into `start_page` and `end_page`.
4. Assigns the legacy chunking version.
5. Applies non-null constraints only after backfill where appropriate.

Do not attempt to infer parent relationships for legacy chunks. Re-ingestion
will create the new hierarchy.

## 6. Extraction and Normalization

### 6.1 Introduce a structured block contract

Replace or extend `ExtractedSection` with an ordered block type containing:

```python
class ExtractedBlock:
    text: str
    block_type: str
    order: int
    start_page: int | None
    end_page: int | None
    heading_level: int | None
    heading_path: list[str]
    metadata: dict
```

The extractor should return a document containing ordered blocks, page count,
and extraction warnings.

### 6.2 PDF extraction

Implement PDF extraction in two stages:

1. A fast text path for digitally generated PDFs.
2. A layout-aware or OCR fallback for pages with insufficient or suspicious
   extracted text.

The first implementation may continue using `pypdf`, but the block contract
must not depend on `pypdf` so a better parser can be introduced later.

Record warnings for:

- Pages with no text.
- Pages with unusually low text density.
- Suspected multi-column reading-order problems.
- OCR fallback usage.

### 6.3 Normalization

Add a deterministic normalization stage:

- Normalize Unicode and whitespace.
- Join words split only by line-end hyphenation.
- Preserve meaningful paragraph breaks.
- Detect repeated page headers and footers.
- Preserve table row and column relationships.
- Track source page ranges while joining blocks.

Normalization must never silently discard a non-repeated content block.

## 7. Parent and Child Chunk Construction

### 7.1 Parent construction

Build parent sections from the heading hierarchy and ordered blocks:

1. Start a new parent at a meaningful heading boundary.
2. Accumulate blocks until the parent target is reached.
3. If no headings exist, group consecutive paragraphs using the parent token
   target.
4. Split oversized parents at paragraph or sentence boundaries.
5. Preserve the complete heading path and page range.

### 7.2 Child construction

Within each parent:

1. Accumulate complete blocks toward the child target.
2. Split oversized paragraphs by sentence.
3. Hard-split only when a single sentence or non-text block exceeds the maximum.
4. Add limited overlap without crossing unrelated section boundaries.
5. Include the heading path in searchable child text or embedding input.
6. Store clean source text separately from enriched embedding text if heading
   prefixes are added.

### 7.3 Cross-page behavior

A child may cross pages when its source paragraph or section continues naturally.
Its locator must contain:

```json
{
  "start_page": 14,
  "end_page": 15,
  "heading_path": ["Security", "Access Control"]
}
```

Page transitions alone must not introduce duplicated content.

### 7.4 Stable ordering and hashing

Assign ordinals after the complete hierarchy is constructed. Calculate content
hashes from normalized content, chunk level, heading path, and chunking version.
This enables idempotency and future incremental re-embedding.

## 8. Ingestion Pipeline Changes

Refactor ingestion into explicit stages:

1. Extract blocks.
2. Normalize blocks.
3. Construct parents.
4. Construct children.
5. Validate coverage and limits.
6. Embed searchable children in batches.
7. Build lexical vectors.
8. Store parents and children transactionally.
9. Optionally generate and store summaries.

### 8.1 Validation

Before committing:

- Ensure every normalized content block belongs to a parent.
- Ensure every parent has at least one child.
- Ensure child offsets are ordered and valid.
- Ensure page ranges are non-decreasing and valid.
- Ensure reconstructed child coverage does not omit non-overlap content.
- Enforce a configurable token and chunk limit.
- Reject or warn when extraction quality is too low.

### 8.2 Failure handling

- Keep the current document status model.
- Store a stage-specific failure reason.
- Do not leave a partially indexed document marked ready.
- Make retries idempotent.
- Delete obsolete chunks only inside the successful replacement transaction.

## 9. Retrieval Changes

### 9.1 Candidate generation

Initial settings:

- Semantic child candidates: 50.
- Lexical child candidates: 50.
- Fused candidates passed to reranking: 20.
- Final evidence budget: configurable by tokens.

Continue using PostgreSQL vector search and full-text search. No new search
engine is required for this phase.

### 9.2 Reranking

Introduce a reranker interface after reciprocal-rank fusion:

```python
async def rerank(query: str, candidates: list[Candidate]) -> list[Candidate]:
    ...
```

Start with a deterministic local implementation if no cross-encoder service is
available. Keep the interface provider-independent so a learned reranker can be
added later.

### 9.3 Diversity

Replace the hard three-chunks-per-document limit with a scoring penalty or
adaptive cap:

- Multi-document searches should retain document diversity.
- Single-document searches should be allowed to return enough evidence from
  that document.
- Duplicate or highly overlapping child chunks should be suppressed.

### 9.4 Context expansion

For each reranked child:

1. Determine whether the child appears incomplete.
2. Fetch the previous and next child in the same parent when helpful.
3. Use parent text when several children from the same parent match.
4. Stop expansion when the evidence token budget is reached.

Avoid automatically adding both neighbors for every candidate.

### 9.5 Adjacent-range merging

Merge selected chunks when they:

- Belong to the same document and parent.
- Are adjacent or overlapping.
- Fit within the remaining evidence budget.

Remove duplicated overlap and retain a combined page range. Assign one evidence
identifier to the merged passage so citation rendering remains clear.

### 9.6 Evidence assembly

Replace fixed-count-only assembly with:

- A maximum number of evidence items.
- A maximum total evidence token count.
- A maximum merged passage size.
- Diversity across documents and parent sections.

Log which candidates were dropped because of token, duplication, or diversity
constraints.

## 10. Broad-Question Support

### 10.1 Query classification

Classify a query as:

- Narrow fact lookup.
- Multi-part or comparative lookup.
- Broad summary or exhaustive analysis.

Use deterministic rules initially and keep the classifier replaceable.

### 10.2 Hierarchical summaries

For broad questions:

1. Retrieve or generate parent-section summaries.
2. Select relevant section summaries.
3. Retrieve supporting child chunks for factual claims and citations.
4. For whole-document summaries, combine all section summaries through a
   map-reduce process rather than ordinary top-k retrieval.

Summaries must record:

- Source parent or document ID.
- Source page range.
- Summary model and prompt version.
- Content hash of the summarized source.

Summary generation should be optional during the first deployment so the core
chunking architecture can ship independently.

## 11. Configuration

Add settings for:

- Chunk tokenizer.
- Child minimum, target, and maximum tokens.
- Child overlap tokens.
- Parent target and maximum tokens.
- Chunking version.
- Semantic and lexical candidate counts.
- Rerank candidate count.
- Evidence token budget.
- Maximum merged passage tokens.
- Neighbor expansion count.
- Summary generation enablement.

Keep legacy character settings temporarily for rollback, then remove them after
all environments have migrated.

Validate that:

- Minimum is less than or equal to target.
- Target is less than or equal to maximum.
- Overlap is less than the minimum.
- Parent target is greater than child maximum.
- Evidence budget can contain at least one maximum-sized child.

## 12. API and Citation Compatibility

- Keep the existing evidence response shape where possible.
- Extend locators with `start_page`, `end_page`, and `heading_path`.
- Continue emitting `page` for a single-page chunk during the compatibility
  period.
- Update citation formatting to show `p. 14` or `pp. 14-15`.
- Do not expose internal parent IDs unless the frontend needs them.

## 13. Observability

Record metrics by document and chunking version:

- Extracted block count.
- Empty or OCR-fallback page count.
- Parent and child counts.
- Child token-size distribution.
- Percentage of cross-page chunks.
- Ingestion and embedding duration.
- Embedding request count.
- Retrieval candidate and reranking latency.
- Neighbor expansion frequency.
- Evidence tokens delivered to generation.
- Retrieval failure or insufficient-evidence rate.

Use structured logs to record retrieval decisions without logging sensitive
document content.

## 14. Testing Strategy

### 14.1 Unit tests

Add tests for:

- Token counting and size constraints.
- Paragraph and sentence boundary selection.
- Heading hierarchy construction.
- Paragraph continuation across pages.
- No overlap across unrelated sections.
- Overlap de-duplication.
- Small and large table behavior.
- Header and footer removal.
- Stable ordinals and hashes.
- Parent-child relationships.
- Neighbor selection.
- Adjacent-range merging.
- Evidence token-budget enforcement.
- Single-document versus multi-document diversity.

### 14.2 Property tests

For generated block sequences, verify:

- No source block disappears.
- Chunk order is stable.
- Page ranges remain valid.
- Chunks stay within maximum tokens unless one indivisible block exceeds it.
- Repeated execution produces identical output.

### 14.3 Integration tests

Cover:

- A simple text PDF.
- A 200-page PDF.
- A paragraph spanning two pages.
- Multi-column content.
- Tables spanning pages.
- A scanned PDF requiring OCR.
- Documents with no headings.
- Mixed PDF, DOCX, Markdown, and text knowledge bases.
- Re-ingestion from the legacy chunking version.

### 14.4 Retrieval evaluation set

Create a versioned evaluation set with:

- Narrow facts.
- Exact names, codes, and numbers for lexical retrieval.
- Paraphrased questions for semantic retrieval.
- Cross-page answers.
- Questions requiring two or more sections.
- Multi-document comparisons.
- Whole-document summaries.
- Questions with no supported answer.

Measure:

- Recall@10 and recall after reranking.
- Mean reciprocal rank.
- Citation page accuracy.
- Answer faithfulness.
- Percentage of questions receiving all required supporting passages.
- Retrieval and end-to-end latency.
- Average evidence token usage.

Compare the new architecture against the current implementation before making
it the default.

## 15. Performance and Capacity Tests

Benchmark at minimum:

- 10-page, 50-page, 200-page, and maximum-size documents.
- Concurrent ingestion jobs.
- Knowledge bases containing many large documents.
- Cold and warm retrieval.

Track:

- Extraction time.
- Peak worker memory.
- Chunk and embedding counts.
- Database storage growth.
- HNSW and lexical query latency.
- Reranker latency.
- Prompt-token growth.

The new design must not load the entire extracted document into multiple
unnecessary copies in worker memory.

## 16. Rollout Strategy

### Phase 0: Baseline

- Build the retrieval evaluation set.
- Record current quality, latency, storage, and ingestion cost.
- Add metrics needed for comparison.

### Phase 1: Structured token-aware chunking

- Introduce the block contract and normalization stage.
- Implement token-aware parent and child construction.
- Preserve page ranges across boundaries.
- Add schema migration and compatibility fields.
- Store and search child chunks.

Feature flag: `CHUNKING_VERSION=legacy|hierarchical_v1`.

### Phase 2: Retrieval expansion

- Increase candidate pools.
- Add reranking interface.
- Add adaptive document diversity.
- Add selective neighbor or parent expansion.
- Merge adjacent evidence.
- Enforce a total evidence token budget.

Feature flag: `HIERARCHICAL_RETRIEVAL_ENABLED`.

### Phase 3: Extraction quality

- Add repeated header and footer removal.
- Introduce layout-aware PDF parsing.
- Add OCR fallback and quality warnings.
- Add table-aware block extraction.

### Phase 4: Broad questions

- Add query classification.
- Generate parent and document summaries.
- Route broad questions through hierarchical summary retrieval.

### Phase 5: Re-indexing and default rollout

- Re-ingest a representative subset of documents.
- Run offline evaluation and canary traffic.
- Compare quality, latency, and cost.
- Make `hierarchical_v1` the default after acceptance criteria pass.
- Re-index remaining legacy documents in bounded background batches.
- Retain rollback support until re-indexing and monitoring are stable.

## 17. Acceptance Criteria

The architecture is ready for default rollout when:

- Cross-page questions retrieve all required context in the evaluation set.
- Citation page ranges are correct.
- No normalized source content is silently omitted.
- Large-document ingestion stays within configured worker memory limits.
- Retrieval quality improves materially over the legacy baseline.
- Broad questions use the summary path rather than pretending a few chunks
  represent the full document.
- P95 retrieval latency remains within the product target.
- Average evidence tokens remain within the configured generation budget.
- Re-ingestion is idempotent and rollback has been tested.
- Existing document deletion, retry, and citation flows continue to work.

## 18. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| More chunks increase embedding and storage cost | Use bounded token targets, content hashes, and child-only embeddings |
| Parent expansion floods the prompt | Enforce a strict evidence token budget |
| Heading detection is unreliable | Fall back to ordered paragraph grouping |
| OCR adds latency and cost | Invoke it only for low-quality pages |
| Repeated overlap produces redundant evidence | Merge adjacent ranges and remove duplicate overlap |
| Reranker becomes a latency bottleneck | Rerank only the fused top candidates and measure separately |
| Migration breaks existing citations | Preserve legacy locator fields during compatibility period |
| Chunk tuning overfits one PDF type | Evaluate across layouts and file formats |
| Re-indexing overloads workers or embedding provider | Use bounded background batches with retry and rate limiting |

## 19. Suggested Implementation Order

1. Add baseline retrieval evaluations and observability.
2. Add token counting and structured block types.
3. Implement normalization and cross-page block continuity.
4. Implement parent and child chunk construction.
5. Add database migration and persistence changes.
6. Update ingestion and compatibility behavior.
7. Implement token-budgeted retrieval and adjacent merging.
8. Add adaptive diversity and selective neighbor expansion.
9. Add reranking.
10. Add layout-aware extraction and OCR fallback.
11. Add hierarchical summaries and broad-query routing.
12. Re-index legacy documents and retire the legacy chunker.

## 20. Deliverables

- Structured extraction and normalization module.
- Token-aware hierarchical chunker.
- Alembic schema migration.
- Updated ingestion pipeline.
- Updated hybrid retrieval and evidence assembler.
- Citation range support.
- Chunking and retrieval feature flags.
- Unit, property, integration, and retrieval evaluation tests.
- Large-document benchmark report.
- Re-indexing command or background job.
- Rollout and rollback runbook.
