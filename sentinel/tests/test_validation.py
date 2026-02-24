import asyncio

from sentinel.domain.services.debt_service import DebtService
from sentinel.workers.background_worker import BackgroundWorker
from sentinel.workers.job_queue import JobQueue


def test_random_text_input_does_not_crash():
    text = "xqv zlrm nptk synthetic phrase"
    result = DebtService().evaluate_debt(text)
    assert result["complexity"] >= 1


def test_non_code_string_does_not_crash():
    text = "this is not python code and should still be analyzed"
    result = DebtService().evaluate_debt(text)
    assert result["complexity"] >= 1


def test_extremely_nested_logic_does_not_crash():
    nested = "def x(v):\n" + "\n".join("    if v > 0:\n        v += 1" for _ in range(500))
    result = DebtService().evaluate_debt(nested)
    assert result["complexity"] > 15


def test_repeated_keywords_do_not_crash():
    repeated = "if if if if if elif for while except and or case"
    result = DebtService().evaluate_debt(repeated)
    assert result["complexity"] >= 1


def test_if_inside_string_literals_does_not_crash():
    code = "def f():\n    return 'if elif for while and or case except'"
    result = DebtService().evaluate_debt(code)
    assert result["complexity"] >= 1


def test_background_worker_processes_one_job_without_deadlock(capsys):
    async def _run() -> None:
        queue = JobQueue()
        worker = BackgroundWorker(queue)
        task = asyncio.create_task(worker.start())
        await queue.enqueue({"repo": "test", "pr_number": 124})
        await asyncio.sleep(2.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())
    output = capsys.readouterr().out
    assert "PR #124 Risk: LOW" in output
