import json
from typing import Any, Sequence
from pydantic import BaseModel
from langchain_core.documents import Document
from sentence_transformers import CrossEncoder


class LangchainReranker:
    """Document compressor that uses `Cohere Rerank API`."""

    name_or_path: str
    _model: Any
    top_n: int
    device: str
    max_length: int
    batch_size: int
    num_workers: int

    def __init__(
            self,
            name_or_path: str,
            top_n: int = 1,
            device: str = "cpu",
            max_length: int = 512,
            batch_size: int = 32,
            num_workers: int = 0,
    ):

        self._model = CrossEncoder(
            model_name=name_or_path, max_length=max_length, device=device
        )
        self.top_n = top_n
        self.name_or_path = name_or_path
        self.device = device
        self.max_length = max_length
        self.batch_size = batch_size
        self.num_workers = num_workers

    def compress_documents(
            self,
            documents: Sequence[Document],
            query: str,
    ) -> Sequence[Document]:
        """
        Compress documents using Cohere's rerank API.

        Args:
            documents: A sequence of documents to compress.
            query: The query to use for compressing the documents.
            callbacks: Callbacks to run during the compression process.

        Returns:
            A sequence of compressed documents.
        """
        if len(documents) == 0:  # to avoid empty api call
            return []
        doc_list = list(documents)
        _docs = [doc["page_content"] for doc in doc_list]
        sentence_pairs = [[query, _doc] for _doc in _docs]
        results = self._model.predict(
            sentences=sentence_pairs,
            batch_size=self.batch_size,
            num_workers=self.num_workers,
            convert_to_tensor=True,
        )
        top_k = self.top_n if self.top_n < len(results) else len(results)

        values, indices = results.topk(top_k)
        final_results = []
        for value, index in zip(values, indices):
            doc = doc_list[index]
            doc.get("metadata")["relevance_score"] = value
            final_results.append(doc)
        return final_results
