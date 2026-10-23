"""
RAG (Retrieval-Augmented Generation) package for DecisionAI.
Strict constraints: No emojis, no em dashes.
"""

from backend.services.rag.chunking import TranscriptChunk, chunk_transcript_segments
from backend.services.rag.hybrid_search import reciprocal_rank_fusion, run_hybrid_search
from backend.services.rag.intent import classify_query_intent, is_decision_query
from backend.services.rag.pipeline import (
    execute_rag_query,
    index_meeting_transcript,
    stream_rag_query,
)
from backend.services.rag.vector_store import ChromaVectorStore, get_vector_store

__all__ = [
    "TranscriptChunk",
    "chunk_transcript_segments",
    "ChromaVectorStore",
    "get_vector_store",
    "run_hybrid_search",
    "reciprocal_rank_fusion",
    "is_decision_query",
    "classify_query_intent",
    "index_meeting_transcript",
    "execute_rag_query",
    "stream_rag_query",
]
