# Evaluation run `test-run-filtered`

- Status: **completed**
- Evaluation set: `executable_v1`
- Documents: 4/4 Ready
- Cases: 2
- Grounded answer path: disabled
- Grounded answer failures: 0
- Isolated Knowledge Base cleaned up: True

## Configuration

- Chunking version: `legacy`
- Chunking config hash: `None`
- Embedding model: `None`
- Retrieval mode: `None`
- Reranker provider: `None`
- Query planning: off
- BM25 hybrid: off
- RRF: k=None semantic=None lexical=None
- Retrieval semantic candidates: None
- Retrieval lexical candidates: None
- Evidence token budget: None

## Ingestion and storage

- Ingestion duration: 0.0 ms
- Chunks: 4
- Embedding inputs: 4
- Embedding tokens: 0
- Ingestion throughput: — Documents/s, — bytes/s
- Storage estimate: 4096 bytes
- Chunking config hash: `None`

## Aggregate metrics

| Metric | Value |
| --- | ---: |
| `recall_at_1` | 1.0000 |
| `recall_at_5` | 1.0000 |
| `recall_at_10` | 1.0000 |
| `recall_after_reranking` | 1.0000 |
| `mean_reciprocal_rank` | 1.0000 |
| `ndcg_at_10` | 1.0000 |
| `citation_page_accuracy` | 1.0000 |
| `citation_precision` | 1.0000 |
| `answer_faithfulness` | not run |
| `all_required_passages_rate` | 1.0000 |
| `filter_correctness` | not run |
| `mean_retrieval_latency_ms` | 8.0000 |
| `retrieval_latency_p95_ms` | 8.0000 |
| `mean_end_to_end_latency_ms` | 12.0000 |
| `end_to_end_latency_p95_ms` | 12.0000 |
| `mean_evidence_tokens` | 40.0000 |
| `retrieval_latency_p50_ms` | 8.0000 |
| `retrieval_latency_p99_ms` | 8.0000 |
| `end_to_end_latency_p50_ms` | 12.0000 |
| `end_to_end_latency_p99_ms` | 12.0000 |

## Metrics by split

| Split | Cases | Recall@10 | MRR@10 | NDCG@10 | p95 retrieval (ms) | Mean evidence tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| train | 2 | 1.0000 | 1.0000 | 1.0000 | 8.0000 | 40.0000 |

## Bootstrap confidence intervals

| Metric | Mean | 95% CI low | 95% CI high |
| --- | ---: | ---: | ---: |
| `all_required_passages_rate` | 1.0000 | 1.0000 | 1.0000 |
| `answer_faithfulness` | — | — | — |
| `citation_page_accuracy` | 1.0000 | 1.0000 | 1.0000 |
| `citation_precision` | 1.0000 | 1.0000 | 1.0000 |
| `end_to_end_latency_p50_ms` | 12.0000 | 12.0000 | 12.0000 |
| `end_to_end_latency_p95_ms` | 12.0000 | 12.0000 | 12.0000 |
| `end_to_end_latency_p99_ms` | 12.0000 | 12.0000 | 12.0000 |
| `filter_correctness` | 0.0000 | 0.0000 | 0.0000 |
| `mean_end_to_end_latency_ms` | 12.0000 | 12.0000 | 12.0000 |
| `mean_evidence_tokens` | 40.0000 | 40.0000 | 40.0000 |
| `mean_reciprocal_rank` | 1.0000 | 1.0000 | 1.0000 |
| `mean_retrieval_latency_ms` | 8.0000 | 8.0000 | 8.0000 |
| `ndcg_at_10` | 1.0000 | 1.0000 | 1.0000 |
| `recall_after_reranking` | 1.0000 | 1.0000 | 1.0000 |
| `recall_at_1` | 1.0000 | 1.0000 | 1.0000 |
| `recall_at_10` | 1.0000 | 1.0000 | 1.0000 |
| `recall_at_5` | 1.0000 | 1.0000 | 1.0000 |
| `retrieval_latency_p50_ms` | 8.0000 | 8.0000 | 8.0000 |
| `retrieval_latency_p95_ms` | 8.0000 | 8.0000 | 8.0000 |
| `retrieval_latency_p99_ms` | 8.0000 | 8.0000 | 8.0000 |

## Cases

| Case | Category | Required passages | Retrieved passages | Pass | Latency (ms) |
| --- | --- | --- | --- | --- | ---: |
| `narrow_leave_limit` | narrow_fact | hr-leave-p3 | hr-leave-p3 | yes | 8.00 |
| `lexical_incident_code` | lexical_exact | security-codes-p8 | security-codes-p8 | yes | 8.00 |
