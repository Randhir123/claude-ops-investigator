from jsonschema import validate

from claude_ops.schemas.incident_report_schema import INCIDENT_REPORT_SCHEMA


def test_valid_incident_report_schema():
    report = {
        "service": "event-data",
        "namespace": "si",
        "severity": "high",
        "symptoms": ["pod restarted"],
        "evidence": [{"source": "pod_events", "detail": "OOMKilled seen", "timestamp": None}],
        "likely_causes": ["memory spike"],
        "ruled_out": ["node pressure not observed"],
        "recommended_next_steps": ["inspect payload size distribution"],
        "requires_human": True,
        "confidence": "medium",
        "unknowns": ["heap dump unavailable"],
    }
    validate(instance=report, schema=INCIDENT_REPORT_SCHEMA)
