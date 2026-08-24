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
        """Find PR units that duplicate an existing unit.

        Both sides are chunked. Comparing the WHOLE PR against each corpus unit
        — the pre-M10 behavior — only fired when the PR was roughly the size and
        shape of one existing unit: a single copied function inside a larger PR
        was diluted by everything around it and scored far below the threshold.
        Chunking the PR too is what makes function-level detection possible.

        Cost is len(new) x len(corpus) cosine comparisons, but only
        len(new) + len(corpus) embeddings: each side is embedded once and reused,
        where the old loop re-embedded every corpus entry on every call. Both
        sides are already bounded upstream (CORPUS_MAX_UNITS, max_units).
        """
        if not new_code.strip() or not existing_code_list:
            return []

        new_units = self.chunk_code_units(new_code) or [new_code]
        new_embeddings = [
            (unit, self.generate_embedding(self.tokenize_code(unit)))
            for unit in new_units
            if unit.strip()
        ]
        existing_embeddings = [
            (unit, self.generate_embedding(self.tokenize_code(unit)))
            for unit in existing_code_list
            if isinstance(unit, str) and unit.strip()
        ]
        if not new_embeddings or not existing_embeddings:
            return []

        findings: list[Finding] = []
        for _, new_embedding in new_embeddings:
            # One finding per PR unit: report only its closest match, so a unit
            # resembling five near-identical corpus entries does not produce five
            # findings for one problem.
            best_unit, best_score = None, 0.0
            for existing_code, existing_embedding in existing_embeddings:
                similarity = self.compute_similarity(new_embedding, existing_embedding)
                if similarity > best_score:
                    best_unit, best_score = existing_code, similarity

            if best_unit is not None and best_score > self.SIMILARITY_THRESHOLD:
                findings.append(
                    Finding(
                        rule="semantic_duplicate",
                        match=best_unit[:100],
                        severity=SeverityLevel.HIGH,
                        finding_type="semantic",
                        similarity_score=round(best_score, 4),
                    )
                )

        return findings
