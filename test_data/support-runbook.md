# Atlas Search Support Runbook

## Documents Missing from Search

If a document does not appear in search results:

1. Confirm that its file size is no greater than 25 MB.
2. Confirm that the file type is supported.
3. Check whether indexing completed successfully.
4. For scanned PDFs, verify that OCR was performed.
5. Retry indexing once.

If the second indexing attempt fails, create a ticket for the Search Platform
team.

## Slow Search

Search latency below two seconds is considered normal.

If the p95 latency exceeds three seconds for 15 consecutive minutes, notify
the on-call engineer.