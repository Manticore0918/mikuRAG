# Executable evaluation corpus v1

This is a deliberately small, wholly synthetic smoke corpus for the executable
mikuRAG evaluation runner. It does not describe a real organization, incident,
employee, credential, customer, or internal policy. `SEC-417` and every policy
statement are fictional test data.

The corpus is redistributable under `CC0-1.0`; see `LICENSE.txt`. Do not replace
these files with production Documents or copied proprietary material. Create a
new version directory when text, IDs, or gold annotations change.

## Stable identity contract

- `document_id` identifies the versioned source fixture.
- `passage_id` identifies the gold retrieval unit used by qrels and metrics.
- `locator_id` identifies the source-specific citation location.
- IDs are lowercase, unique within the corpus version, and must not be recycled.
- Database Document/chunk UUIDs and run IDs are intentionally not stable.

For this Markdown-only smoke set, locator IDs use
`markdown:<document-id>#<heading-slug>`. Future PDF, HTML, and code corpus
versions should use equally explicit page, DOM/element, or path/symbol locator
schemes without changing this version.
