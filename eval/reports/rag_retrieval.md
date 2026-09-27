# Retrieval eval (M2-08)

The clinic handbook (fictional, services/rag/handbook), retrieved for labelled questions in English and Arabic. Research prototype; synthetic data only.

- Generated 2026-09-27T18:38:39+00:00; dataset `eval/datasets/rag/retrieval.jsonl` (sha256 `371708d0e7fec701…`): 30 + 30 answerable questions (English + Arabic) and 5 + 5 out of scope, written by the coding agent (not real users).
- Handbook version `cdbc0b73e771d50b`: 25 sections per language, 50 chunks.
- Embedder `Qwen/Qwen3-Embedding-0.6B@97b0c614` on mps (sentence-transformers 5.7.0, transformers 5.17.0, torch 2.14.0); pgvector 0.8.6, pg_textsearch 1.4.0.
- hit@5: a gold section in the top 5. hit@1: first. MRR: 1 / rank of the first gold section in the top 20. RRF k = 60.

| Language | Mode | hit@5 | hit@1 | MRR | Missed at 5 | Median time |
| --- | --- | --- | --- | --- | --- | --- |
| en | dense | 1.000 | 0.933 | 0.967 | none | 34.3 ms |
| en | bm25 | 0.800 | 0.433 | 0.586 | `en-05`, `en-07`, `en-09`, `en-10`, `en-11`, `en-29` | 1.6 ms |
| en | hybrid | 0.967 | 0.800 | 0.877 | `en-29` | 32.9 ms |
| ar | dense | 1.000 | 0.867 | 0.922 | none | 33.4 ms |
| ar | bm25 | 0.933 | 0.567 | 0.719 | `ar-09`, `ar-30` | 2.1 ms |
| ar | hybrid | 1.000 | 0.867 | 0.917 | none | 35.3 ms |

Target: hybrid hit@5 >= 0.90 in both languages, met (a target on these questions, not a claim).

## The best chunk's dense cosine

For M2-09's "I don't know" threshold (configured `min_score` 0.45, mode `dense`): the best dense cosine among the top 5, in dense mode.

| Language | Top-1 hits | Top-1 misses | Out of scope | Answerable below 0.45 | Out of scope at or above 0.45 |
| --- | --- | --- | --- | --- | --- |
| en | 28; 0.370 / 0.586 / 0.794 | 2; 0.367 / 0.387 / 0.406 | 5; 0.175 / 0.201 / 0.253 | 5 | 0 |
| ar | 26; 0.412 / 0.636 / 0.848 | 4; 0.351 / 0.388 / 0.547 | 5; 0.193 / 0.264 / 0.347 | 4 | 0 |

Cells: n; min / median / max. A small corpus (25 sections per language): every chunk is in the dense top 20, so these numbers say little about a larger handbook.
