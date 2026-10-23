"""
Hybrid Search Module: Combines Dense Vector Search and Sparse BM25 Keyword Search.
Uses Reciprocal Rank Fusion (RRF) for balanced retrieval.
Strict constraints: No emojis, no em dashes.
"""

import math
import re
from typing import Any


def tokenize_for_bm25(text: str) -> list[str]:
    """
    Tokenize multilingual text for BM25 keyword matching.
    Preserves alphanumeric sequences and Devanagari characters.
    """
    if not text:
        return []
    # Match words in Latin/English, Devanagari (Hindi \u0900-\u097F), numbers
    tokens = re.findall(r"[\w\u0900-\u097F]+", text.lower())
    return [t for t in tokens if len(t) > 1]


class SimpleBM25:
    """
    Self-contained BM25Okapi implementation to ensure robust keyword retrieval
    with zero hard dependencies, matching rank_bm25 interface.
    """

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.corpus_size = len(corpus_tokens)
        self.avgdl = sum(len(doc) for doc in corpus_tokens) / self.corpus_size if self.corpus_size > 0 else 0
        self.doc_freqs: dict[str, int] = {}
        self.doc_lens = [len(doc) for doc in corpus_tokens]
        self.doc_term_freqs: list[dict[str, int]] = []

        for doc in corpus_tokens:
            tf: dict[str, int] = {}
            for term in doc:
                tf[term] = tf.get(term, 0) + 1
            self.doc_term_freqs.append(tf)
            for term in set(doc):
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1

        self.idf: dict[str, float] = {}
        for term, freq in self.doc_freqs.items():
            self.idf[term] = math.log((self.corpus_size - freq + 0.5) / (freq + 0.5) + 1.0)

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        for term in query_tokens:
            if term not in self.idf:
                continue
            idf_val = self.idf[term]
            for idx in range(self.corpus_size):
                tf = self.doc_term_freqs[idx].get(term, 0)
                if tf <= 0:
                    continue
                num = tf * (self.k1 + 1)
                denom = tf + self.k1 * (1 - self.b + self.b * (self.doc_lens[idx] / (self.avgdl or 1)))
                scores[idx] += idf_val * (num / denom)
        return scores


def reciprocal_rank_fusion(
    vector_results: list[dict[str, Any]],
    bm25_results: list[dict[str, Any]],
    k_constant: int = 60,
    vector_weight: float = 0.6,
    bm25_weight: float = 0.4,
) -> list[dict[str, Any]]:
    """
    Reciprocal Rank Fusion (RRF) algorithm to combine dense vector and sparse BM25 rankings.
    Formula: score = weight / (k + rank)
    """
    rrf_scores: dict[str, float] = {}
    chunk_map: dict[str, dict[str, Any]] = {}

    # Add dense ranks
    for rank, item in enumerate(vector_results, start=1):
        cid = item["chunk_id"]
        chunk_map[cid] = item
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (vector_weight / (k_constant + rank))

    # Add BM25 ranks
    for rank, item in enumerate(bm25_results, start=1):
        cid = item["chunk_id"]
        if cid not in chunk_map:
            chunk_map[cid] = item
        rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (bm25_weight / (k_constant + rank))

    # Sort merged results by fused score descending
    sorted_items = sorted(
        rrf_scores.items(),
        key=lambda pair: pair[1],
        reverse=True,
    )

    fused_results = []
    for cid, score in sorted_items:
        entry = dict(chunk_map[cid])
        entry["rrf_score"] = round(score, 6)
        fused_results.append(entry)

    return fused_results


def run_hybrid_search(
    query: str,
    vector_hits: list[dict[str, Any]],
    all_user_chunks: list[dict[str, Any]],
    top_k: int = 7,
) -> list[dict[str, Any]]:
    """
    Execute hybrid search over vector hits and the user's chunk corpus.
    """
    if not all_user_chunks:
        return vector_hits[:top_k]

    # Prepare BM25 corpus
    corpus_tokens = [tokenize_for_bm25(c.get("text", "")) for c in all_user_chunks]
    query_tokens = tokenize_for_bm25(query)

    bm25_hits: list[dict[str, Any]] = []
    if query_tokens and corpus_tokens:
        try:
            from rank_bm25 import BM25Okapi
            bm25 = BM25Okapi(corpus_tokens)
            scores = bm25.get_scores(query_tokens)
        except Exception:
            bm25 = SimpleBM25(corpus_tokens)
            scores = bm25.get_scores(query_tokens)

        scored_chunks = []
        for idx, score in enumerate(scores):
            if score > 0.01:
                chunk_copy = dict(all_user_chunks[idx])
                chunk_copy["bm25_score"] = float(score)
                scored_chunks.append(chunk_copy)

        scored_chunks.sort(key=lambda x: x["bm25_score"], reverse=True)
        bm25_hits = scored_chunks[: top_k * 2]

    # Fuse vector and BM25 results
    fused = reciprocal_rank_fusion(vector_results=vector_hits, bm25_results=bm25_hits)
    return fused[:top_k]
