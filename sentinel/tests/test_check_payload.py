"""M6: AuditOrchestrator.build_check_payload — findings -> Checks API shape."""

from sentinel.application.audit_orchestrator import AuditOrchestrator
from sentinel.domain.entities.finding import Finding
from sentinel.domain.value_objects.severity_level import SeverityLevel


def _orchestrator() -> AuditOrchestrator:
    return AuditOrchestrator(queue=None)


def _security(line: int, severity: SeverityLevel = SeverityLevel.HIGH) -> Finding:
    return Finding(
        rule="password_assignment",
        match="x",
        severity=severity,
        description="Hardcoded password detected.",
        line=line,
        recommendation="Load it from the environment.",
    )


def _doc(file_name: str, severity: SeverityLevel = SeverityLevel.MEDIUM) -> Finding:
    return Finding(
        rule="missing_installation",
        match=file_name,
        severity=severity,
        finding_type="documentation",
        description="Docs lack installation instructions.",
        file=file_name,
        line=1,
    )


def test_conclusion_mapping():
    orchestrator = _orchestrator()
    assert orchestrator.build_check_payload([], SeverityLevel.CRITICAL)["conclusion"] == "failure"
    assert orchestrator.build_check_payload([], SeverityLevel.HIGH)["conclusion"] == "failure"
    assert orchestrator.build_check_payload([], SeverityLevel.MEDIUM)["conclusion"] == "neutral"
    assert orchestrator.build_check_payload([], SeverityLevel.LOW)["conclusion"] == "success"
    assert orchestrator.build_check_payload([], "high")["conclusion"] == "failure"  # str risk


def test_security_annotation_resolved_via_line_map():
    # Lists (not tuples) on purpose: that's the shape after a Redis JSON round-trip.
    line_map = [["app.py", 14], ["app.py", 15]]
    payload = _orchestrator().build_check_payload(
        [_security(line=2)], SeverityLevel.HIGH, line_map=line_map
    )

    (annotation,) = payload["annotations"]
    assert annotation["path"] == "app.py"
    assert annotation["start_line"] == 15
    assert annotation["end_line"] == 15
    assert annotation["annotation_level"] == "failure"
    assert "Hardcoded password" in annotation["message"]
    assert "Load it from the environment." in annotation["message"]
    assert annotation["title"] == "password_assignment"


def test_doc_annotation_uses_its_own_location_and_level():
    payload = _orchestrator().build_check_payload([_doc("README.md")], SeverityLevel.MEDIUM)
    (annotation,) = payload["annotations"]
    assert annotation["path"] == "README.md"
    assert annotation["start_line"] == 1
    assert annotation["annotation_level"] == "warning"  # MEDIUM


def test_unmappable_findings_are_skipped_not_fatal():
    semantic = Finding(
        rule="semantic_duplicate",
        match="dup",
        severity=SeverityLevel.HIGH,
        finding_type="semantic",
        similarity_score=0.95,
    )
    unmapped_security = _security(line=7)  # no line_map supplied
    out_of_range = _security(line=99)

    payload = _orchestrator().build_check_payload(
        [semantic, unmapped_security, out_of_range],
        SeverityLevel.HIGH,
        line_map=[["app.py", 3]],
    )
    assert payload["annotations"] == []
    assert payload["conclusion"] == "failure"  # conclusion unaffected


def test_annotation_cap_and_overflow_note():
    findings = [_doc(f"file_{i}.md") for i in range(55)]
    payload = _orchestrator().build_check_payload(findings, SeverityLevel.MEDIUM)
    assert len(payload["annotations"]) == AuditOrchestrator.MAX_CHECK_ANNOTATIONS
    assert "(+5 more" in payload["summary"]


def test_title_and_summary_counts():
    findings = [_security(1), _security(2), _doc("README.md")]
    payload = _orchestrator().build_check_payload(
        findings,
        SeverityLevel.HIGH,
        line_map=[["a.py", 1], ["a.py", 2]],
        semantic_findings_count=3,
    )
    assert payload["title"] == "Risk: HIGH — 2 security, 1 documentation issue(s)"
    assert "**Risk score:** HIGH" in payload["summary"]
    assert "Semantic duplicates: 3" in payload["summary"]
