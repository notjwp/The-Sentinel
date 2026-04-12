from sentinel.domain.services.document_service import DocumentService


class _DocReviewer:
    def explain_issue_safe(self, code: str, issue: str, *, severity=None) -> str:
        _ = code
        _ = issue
        _ = severity
        return "Documentation could be clearer and include more examples."


def test_document_service_detects_empty_readme():
    service = DocumentService()

    findings = service.analyze(["README.md"], {"README.md": ""})

    rules = {finding.rule for finding in findings}
    assert "empty_readme" in rules


def test_document_service_detects_missing_installation_and_usage():
    service = DocumentService()
    content = "This document describes architecture only."

    findings = service.analyze(["guide.md"], {"guide.md": content})

    rules = {finding.rule for finding in findings}
    assert "missing_installation" in rules
    assert "missing_usage" in rules


def test_document_service_ignores_non_document_files():
    service = DocumentService()

    findings = service.analyze(["src/main.py"], {"src/main.py": "print('ok')"})

    assert findings == []


def test_document_service_optional_llm_review_adds_doc_clarity_finding():
    service = DocumentService()
    reviewer = _DocReviewer()

    findings = service.analyze(
        ["README.md"],
        {"README.md": "Installation\nUse pip install.\nUsage\nRun main.py."},
        enable_llm_review=True,
        llm_reviewer=reviewer,
    )

    rules = {finding.rule for finding in findings}
    assert "doc_clarity_review" in rules
