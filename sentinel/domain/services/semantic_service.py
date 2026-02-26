import re
from typing import Protocol

from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class EmbeddingPort(Protocol):
    def generate_embedding(self, text: str) -> list[float]: ...
    def compute_similarity(self, vec1: list[float], vec2: list[float]) -> float: ...


class SemanticService:
    SIMILARITY_THRESHOLD: float = 0.9

    def __init__(self, embedding_engine: EmbeddingPort) -> None:
        self._embedding_engine = embedding_engine

    def tokenize_code(self, code: str) -> list[str]:
        raw_tokens = re.split(r"[^a-zA-Z0-9]+", code.lower())
        return [token for token in raw_tokens if token]

    def generate_embedding(self, tokens: list[str]) -> list[float]:
        text = " ".join(tokens)
        return self._embedding_engine.generate_embedding(text)

    def compute_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        return self._embedding_engine.compute_similarity(vec1, vec2)

    def detect_duplicates(
        self, new_code: str, existing_code_list: list[str]
    ) -> list[Finding]:
        if not new_code.strip() or not existing_code_list:
            return []

        new_tokens = self.tokenize_code(new_code)
        new_embedding = self.generate_embedding(new_tokens)
        findings: list[Finding] = []

        for existing_code in existing_code_list:
            if not existing_code.strip():
                continue
            existing_tokens = self.tokenize_code(existing_code)
            existing_embedding = self.generate_embedding(existing_tokens)
            similarity = self.compute_similarity(new_embedding, existing_embedding)

            if similarity > self.SIMILARITY_THRESHOLD:
                findings.append(
                    Finding(
                        rule="semantic_duplicate",
                        match=existing_code[:100],
                        severity=SeverityLevel.HIGH,
                        finding_type="semantic",
                        similarity_score=round(similarity, 4),
                    )
                )

        return findings
