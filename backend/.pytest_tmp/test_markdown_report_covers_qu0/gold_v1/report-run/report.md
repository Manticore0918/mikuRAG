# Evaluation run `report-run`

- Status: **completed**
- Evaluation set: `gold_v1`
- Documents: 0/0 Ready
- Cases: 4
- Grounded answer path: enabled
- Grounded answer failures: 0
- Isolated Knowledge Base cleaned up: True

## Configuration

- Chunking version: `token_recursive_v1`
- Chunking config hash: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`
- Embedding model: `mock-embed`
- Retrieval mode: `None`
- Reranker provider: `None`
- Query planning: off
- BM25 hybrid: off
- RRF: k=None semantic=None lexical=None
- Retrieval semantic candidates: None
- Retrieval lexical candidates: None
- Evidence token budget: None

## Ingestion and storage

- Ingestion duration: 250.0 ms
- Chunks: 12
- Embedding inputs: 8
- Embedding tokens: 0
- Ingestion throughput: 0.0 Documents/s, 0.0 bytes/s
- Storage estimate: 2048 bytes
- Chunking config hash: `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa`

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
| `answer_faithfulness` | 1.0000 |
| `all_required_passages_rate` | 1.0000 |
| `filter_correctness` | not run |
| `mean_retrieval_latency_ms` | 10.0000 |
| `retrieval_latency_p95_ms` | 10.0000 |
| `mean_end_to_end_latency_ms` | 15.0000 |
| `end_to_end_latency_p95_ms` | 15.0000 |
| `mean_evidence_tokens` | 100.0000 |
| `retrieval_latency_p50_ms` | 10.0000 |
| `retrieval_latency_p99_ms` | 10.0000 |
| `end_to_end_latency_p50_ms` | 15.0000 |
| `end_to_end_latency_p99_ms` | 15.0000 |

## Metrics by split

| Split | Cases | Recall@10 | MRR@10 | NDCG@10 | p95 retrieval (ms) | Mean evidence tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| test | 3 | 1.0000 | 1.0000 | 1.0000 | 10.0000 | 100.0000 |
| train | 1 | 1.0000 | 1.0000 | 1.0000 | 10.0000 | 100.0000 |

## Bootstrap confidence intervals

| Metric | Mean | 95% CI low | 95% CI high |
| --- | ---: | ---: | ---: |
| `all_required_passages_rate` | 1.0000 | 1.0000 | 1.0000 |
| `answer_faithfulness` | 1.0000 | 1.0000 | 1.0000 |
| `citation_page_accuracy` | 1.0000 | 1.0000 | 1.0000 |
| `citation_precision` | 1.0000 | 1.0000 | 1.0000 |
| `end_to_end_latency_p50_ms` | 15.0000 | 15.0000 | 15.0000 |
| `end_to_end_latency_p95_ms` | 15.0000 | 15.0000 | 15.0000 |
| `end_to_end_latency_p99_ms` | 15.0000 | 15.0000 | 15.0000 |
| `filter_correctness` | 0.0000 | 0.0000 | 0.0000 |
| `mean_end_to_end_latency_ms` | 15.0000 | 15.0000 | 15.0000 |
| `mean_evidence_tokens` | 100.0000 | 100.0000 | 100.0000 |
| `mean_reciprocal_rank` | 1.0000 | 1.0000 | 1.0000 |
| `mean_retrieval_latency_ms` | 10.0000 | 10.0000 | 10.0000 |
| `ndcg_at_10` | 1.0000 | 1.0000 | 1.0000 |
| `recall_after_reranking` | 1.0000 | 1.0000 | 1.0000 |
| `recall_at_1` | 1.0000 | 1.0000 | 1.0000 |
| `recall_at_10` | 1.0000 | 1.0000 | 1.0000 |
| `recall_at_5` | 1.0000 | 1.0000 | 1.0000 |
| `retrieval_latency_p50_ms` | 10.0000 | 10.0000 | 10.0000 |
| `retrieval_latency_p95_ms` | 10.0000 | 10.0000 | 10.0000 |
| `retrieval_latency_p99_ms` | 10.0000 | 10.0000 | 10.0000 |

## Cases

| Case | Category | Required passages | Retrieved passages | Pass | Latency (ms) |
| --- | --- | --- | --- | --- | ---: |
| `case-0` | narrow_fact | a | a | yes | 10.00 |
| `case-1` | narrow_fact | a | a | yes | 10.00 |
| `case-2` | narrow_fact | a | a | yes | 10.00 |
| `train-case` | narrow_fact | a | a | yes | 10.00 |
