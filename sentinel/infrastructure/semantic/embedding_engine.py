from sklearn.feature_extraction.text import HashingVectorizer
from sklearn.metrics.pairwise import cosine_similarity as sklearn_cosine_similarity


class EmbeddingEngine:
    def __init__(self, n_features: int = 128) -> None:
        self._vectorizer = HashingVectorizer(
            n_features=n_features,
            alternate_sign=False,
            norm="l2",
        )

    def generate_embedding(self, text: str) -> list[float]:
        vector = self._vectorizer.transform([text])
        return vector.toarray()[0].tolist()

    def compute_similarity(self, vec1: list[float], vec2: list[float]) -> float:
        result = sklearn_cosine_similarity([vec1], [vec2])
        return float(result[0][0])
