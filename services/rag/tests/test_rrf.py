"""Reciprocal rank fusion (M2-08): `score = sum(1 / (60 + rank))`, on ranks only."""

from __future__ import annotations

import pytest

from careflow_rag.retrieve import RRF_K, rrf

A, B, C = ("a.one", 0), ("b.two", 0), ("c.three", 0)


def test_rrf_adds_one_over_k_plus_rank_for_each_list() -> None:
    scores = rrf([[A, B, C], [C, A]])
    assert scores[A] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[B] == pytest.approx(1 / 62)
    assert scores[C] == pytest.approx(1 / 63 + 1 / 61)
    assert RRF_K == 60


def test_rrf_orders_a_chunk_found_by_both_lists_above_one_found_by_one() -> None:
    scores = rrf([[A, B], [B]])
    assert sorted(scores, key=lambda key: -scores[key]) == [B, A]


def test_rrf_uses_ranks_only() -> None:
    """The same order gives the same scores, whatever the raw scores were (cosine similarities,
    or BM25 scores that are negative): only the positions go in."""
    assert rrf([[A, B, C]]) == rrf([[A, B, C]], k=60)
    assert rrf([[A, B, C]], k=1) == {A: 1 / 2, B: 1 / 3, C: 1 / 4}


def test_rrf_of_no_lists_is_empty() -> None:
    assert rrf([]) == {}
    assert rrf([[], []]) == {}
