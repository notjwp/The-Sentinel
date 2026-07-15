import re
from typing import Protocol

from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


class EmbeddingPort(Protocol):
    def generate_embedding(self, text: str) -> list[float]: ...
    def compute_similarity(self, vec1: list[float], vec2: list[float]) -> float: ...


class SemanticService:
    SIMILARITY_THRESHOLD: float = 0.9
    # Corpus chunks smaller than this embed noisily; drop them.
    MIN_CHUNK_TOKENS: int = 5

    def __init__(self, embedding_engine: EmbeddingPort) -> None:
        self._embedding_engine = embedding_engine

    @staticmethod
    def tokenize_code(code: str) -> list[str]:
        raw_tokens = re.split(r"[^a-zA-Z0-9]+", code.lower())
        return [token for token in raw_tokens if token]

    @staticmethod
    def chunk_code_units(content: str, *, max_units: int = 50) -> list[str]:
        """Split source text into top-level def/class units for corpus granularity.

        detect_duplicates embeds the whole PR against each corpus entry, so a PR
        that re-implements one existing function only crosses the similarity
        threshold against an entry of roughly that function's size — per-unit
        entries beat whole files. Decorators stay attached to their unit; the
        preamble (imports/constants before the first unit) forms its own chunk;
        fragments under MIN_CHUNK_TOKENS are dropped.
        """
        if not isinstance(content, str) or not content.strip():
            return []

        lines = content.splitlines()
        starts: list[int] = []
        for index, line in enumerate(lines):
            if line.startswith(("def ", "class ")):
                while index > 0 and lines[index - 1].startswith("@"):
                    index -= 1
                if not starts or index > starts[-1]:
                    starts.append(index)

        boundaries = starts if starts and starts[0] == 0 else [0, *starts]
        chunks: list[str] = []
        for position, begin in enumerate(boundaries):
            end = boundaries[position + 1] if position + 1 < len(boundaries) else len(lines)
            chunk = "\n".join(lines[begin:end]).strip()
            if len(SemanticService.tokenize_code(chunk)) >= SemanticService.MIN_CHUNK_TOKENS:
                chunks.append(chunk)
            if len(chunks) >= max_units:
                break
        return chunks

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
