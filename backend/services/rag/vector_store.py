"""
ChromaDB vector store and local multilingual embedding pipeline.
Stores and queries transcript chunks with strict user-scoping.
Strict constraints: No emojis, no em dashes.
"""

import hashlib
import logging
import math
from typing import Any, Sequence

import chromadb
from chromadb.api.types import EmbeddingFunction, Embeddings, Documents

from backend.config import Settings, get_settings
from backend.services.rag.chunking import TranscriptChunk

logger = logging.getLogger(__name__)


class MultilingualEmbeddingFunction(EmbeddingFunction):
    """
    Embedding function using sentence-transformers multilingual model.
    Falls back gracefully to deterministic feature embeddings when offline.
    """

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2", local_files_only: bool | None = None):
        self.model_name = model_name
        self.local_files_only = local_files_only
        self._model = None
        self._init_failed = False

    def _get_model(self):
        if self._model is None and not self._init_failed:
            import os
            try:
                from sentence_transformers import SentenceTransformer
                # Load model locally; avoid hanging on unauthenticated network downloads
                allow_online = os.environ.get("HF_HUB_ONLINE", "0") == "1"
                local_only = not allow_online if self.local_files_only is None else self.local_files_only
                self._model = SentenceTransformer(self.model_name, local_files_only=local_only)
                logger.info(f"Loaded embedding model: {self.model_name}")
            except Exception as e:
                logger.warning(
                    f"Could not load SentenceTransformer '{self.model_name}' ({e}). "
                    "Using deterministic multilingual fallback embeddings."
                )
                self._init_failed = True
        return self._model

    def __call__(self, input: Documents) -> Embeddings:
        model = self._get_model()
        if model is not None:
            try:
                embeddings = model.encode(input, convert_to_numpy=True, normalize_embeddings=True)
                return embeddings.tolist()
            except Exception as err:
                logger.warning(f"Embedding error: {err}. Falling back to deterministic embeddings.")

        # Deterministic fallback: 384-dimensional normalized hashing representation
        dim = 384
        result: list[list[float]] = []
        for doc in input:
            vec = [0.0] * dim
            tokens = doc.lower().split()
            if not tokens:
                tokens = ["empty"]
            for token in tokens:
                h = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16)
                idx = h % dim
                sign = 1.0 if ((h >> 8) & 1) else -1.0
                vec[idx] += sign
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            result.append([round(x / norm, 6) for x in vec])
        return result


class ChromaVectorStore:
    """
    Persistent ChromaDB vector store for transcript chunks.
    Enforces user_id filtering on every query.
    """

    def __init__(self, settings: Settings | None = None, persist_dir: str | None = None):
        self.settings = settings or get_settings()
        self.persist_dir = persist_dir or self.settings.chroma_persist_dir

        # Initialize persistent Chroma client
        self.client = chromadb.PersistentClient(path=self.persist_dir)
        self.embedding_fn = MultilingualEmbeddingFunction(self.settings.embedding_model)

        self.collection = self.client.get_or_create_collection(
            name="meeting_transcripts",
            embedding_function=self.embedding_fn,
            metadata={"description": "DecisionAI meeting transcript chunks"},
        )

    def upsert_chunks(self, chunks: Sequence[TranscriptChunk]) -> int:
        """
        Upsert transcript chunks into ChromaDB with metadata.
        """
        if not chunks:
            return 0

        ids = [c.chunk_id for c in chunks]
        documents = [c.text for c in chunks]
        metadatas = [
            {
                "user_id": int(c.user_id),
                "meeting_id": int(c.meeting_id),
                "meeting_title": str(c.meeting_title),
                "date": str(c.date),
                "speakers": str(c.speakers),
                "start_ms": int(c.start_ms),
                "end_ms": int(c.end_ms),
                "chunk_index": int(c.chunk_index),
            }
            for c in chunks
        ]

        self.collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        logger.info(f"Upserted {len(chunks)} chunks to ChromaDB.")
        return len(chunks)

    def search(
        self,
        query: str,
        user_id: int,
        meeting_id: int | None = None,
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search similar chunks in ChromaDB with strict user_id scoping.
        """
        if not query.strip():
            return []

        # Enforce user_id filter
        if meeting_id is not None:
            where_clause = {
                "$and": [
                    {"user_id": int(user_id)},
                    {"meeting_id": int(meeting_id)},
                ]
            }
        else:
            where_clause = {"user_id": int(user_id)}

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"ChromaDB search error: {e}", exc_info=True)
            return []

        hits: list[dict[str, Any]] = []
        if not results or not results.get("ids") or not results["ids"][0]:
            return hits

        ids = results["ids"][0]
        docs = results["documents"][0] if results.get("documents") else []
        metas = results["metadatas"][0] if results.get("metadatas") else []
        distances = results["distances"][0] if results.get("distances") else []

        for i in range(len(ids)):
            meta = metas[i] if i < len(metas) else {}
            dist = distances[i] if i < len(distances) else 1.0
            # Convert L2/cosine distance to similarity score
            score = 1.0 / (1.0 + max(0.0, float(dist)))
            hits.append({
                "chunk_id": ids[i],
                "text": docs[i] if i < len(docs) else "",
                "metadata": meta,
                "score": round(score, 4),
                "distance": round(float(dist), 4),
            })

        return hits

    def get_user_chunks(
        self,
        user_id: int,
        meeting_id: int | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """
        Retrieve all chunks belonging to a user for sparse BM25 indexing.
        """
        if meeting_id is not None:
            where_clause = {
                "$and": [
                    {"user_id": int(user_id)},
                    {"meeting_id": int(meeting_id)},
                ]
            }
        else:
            where_clause = {"user_id": int(user_id)}

        try:
            res = self.collection.get(
                where=where_clause,
                limit=limit,
                include=["documents", "metadatas"],
            )
            items: list[dict[str, Any]] = []
            ids = res.get("ids", [])
            docs = res.get("documents", [])
            metas = res.get("metadatas", [])
            for i in range(len(ids)):
                items.append({
                    "chunk_id": ids[i],
                    "text": docs[i] if i < len(docs) else "",
                    "metadata": metas[i] if i < len(metas) else {},
                })
            return items
        except Exception as e:
            logger.error(f"Error fetching user chunks: {e}", exc_info=True)
            return []

    def delete_meeting(self, meeting_id: int, user_id: int) -> None:
        """
        Delete all chunks associated with a specific meeting.
        """
        try:
            self.collection.delete(
                where={
                    "$and": [
                        {"user_id": int(user_id)},
                        {"meeting_id": int(meeting_id)},
                    ]
                }
            )
            logger.info(f"Deleted chunks for meeting #{meeting_id} (user {user_id}).")
        except Exception as e:
            logger.error(f"Error deleting meeting chunks from ChromaDB: {e}", exc_info=True)


# Global singleton factory
_vector_store_instance: ChromaVectorStore | None = None


def get_vector_store(settings: Settings | None = None) -> ChromaVectorStore:
    global _vector_store_instance
    if settings is not None and settings.chroma_persist_dir:
        if _vector_store_instance is None or _vector_store_instance.persist_dir != settings.chroma_persist_dir:
            _vector_store_instance = ChromaVectorStore(settings=settings)
            return _vector_store_instance
    if _vector_store_instance is None:
        _vector_store_instance = ChromaVectorStore(settings=settings)
    return _vector_store_instance

