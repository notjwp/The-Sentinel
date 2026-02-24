import time
from pathlib import Path

from sentinel.domain.services.debt_service import DebtService
from sentinel.domain.value_objects.severity_level import SeverityLevel

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_debt_service_1000_evaluations_under_threshold_and_stable_growth():
    code = (FIXTURES_DIR / "complex_function.py").read_text(encoding="utf-8")
    service = DebtService()

    batch_durations: list[float] = []
    first_result = None

    total_start = time.perf_counter()
    for _ in range(10):
        batch_start = time.perf_counter()
        for _ in range(100):
            result = service.evaluate_debt(code)
            if first_result is None:
                first_result = result
            assert result == first_result
            assert result["complexity"] >= 1
            assert 0 <= result["maintainability"] <= 100
            assert result["severity"] in {SeverityLevel.LOW, SeverityLevel.MEDIUM, SeverityLevel.HIGH}
        batch_durations.append(time.perf_counter() - batch_start)
    total_duration = time.perf_counter() - total_start

    assert total_duration < 2.0
    assert batch_durations[-1] <= batch_durations[0] * 3
