"""Tests for EvidencePanel widget and format_evidence_panel."""

from __future__ import annotations

from forge.tui.widgets.evidence_panel import EvidencePanel, format_evidence_panel


class TestFormatEvidencePanelNoData:
    def test_format_evidence_panel_no_data(self):
        result = format_evidence_panel({}, "some task")
        assert "WHY THESE FILES?" in result
        assert "some task" in result
        assert "No retrieval data" in result
        assert "snapshot fallback" in result

    def test_format_evidence_panel_none_diagnostics(self):
        result = format_evidence_panel({}, "")
        assert "No retrieval data" in result


class TestFormatEvidencePanelNoRetrieval:
    def test_format_evidence_panel_no_retrieval(self):
        diag = {"used_retrieval": False, "stage": "agent"}
        result = format_evidence_panel(diag, "My Task")
        assert "No retrieval data" in result
        assert "snapshot fallback" in result


class TestFormatEvidencePanelWithEvidenceFiles:
    def test_format_evidence_panel_with_evidence_files(self):
        diag = {
            "used_retrieval": True,
            "confidence": 0.85,
            "matched_terms": ["auth", "login"],
            "missed_terms": ["obscure"],
            "evidence_files": [
                {
                    "path": "src/auth.py",
                    "rank": 1,
                    "focus_range": [10, 50],
                    "reasons": ["import_match", "symbol_hit"],
                    "symbols": [
                        {"name": "authenticate", "line": 15},
                        {"name": "login", "line": 30},
                    ],
                    "neighbors": [
                        {"kind": "imports", "path": "src/utils.py"},
                    ],
                },
                {
                    "path": "src/models.py",
                    "rank": 4,
                    "focus_range": None,
                    "reasons": ["co_changed"],
                    "symbols": [{"name": "User", "line": 5}],
                    "neighbors": [],
                },
            ],
        }
        result = format_evidence_panel(diag, "Add authentication")
        assert "WHY THESE FILES?" in result
        assert "Add authentication" in result
        assert "85%" in result
        assert "auth" in result
        assert "login" in result
        assert "obscure" in result
        assert "src/auth.py" in result
        assert "L10-L50" in result
        assert "import_match" in result
        assert "authenticate" in result
        assert "L15" in result
        assert "imports src/utils.py" in result
        assert "src/models.py" in result
        assert "#22c55e" in result  # rank 1 green
        assert "#8b949e" in result  # rank 4+ gray


class TestFormatEvidencePanelFallbackToTopFiles:
    def test_format_evidence_panel_fallback_to_top_files(self):
        diag = {
            "used_retrieval": True,
            "confidence": 0.7,
            "matched_terms": ["search"],
            "missed_terms": [],
            "evidence_files": [],
            "top_files": ["a.py", "b.py", "c.py"],
        }
        result = format_evidence_panel(diag, "Search feature")
        assert "a.py" in result
        assert "b.py" in result
        assert "c.py" in result


class TestEvidencePanelToggle:
    def test_evidence_panel_toggle(self):
        panel = EvidencePanel()
        assert panel.is_open is False
        panel.toggle()
        assert panel.is_open is True
        panel.toggle()
        assert panel.is_open is False


class TestFormatEvidencePanelConfidenceDisplay:
    def test_format_evidence_panel_confidence_display(self):
        diag = {
            "used_retrieval": True,
            "confidence": 0.923,
            "matched_terms": [],
            "missed_terms": [],
            "evidence_files": [],
            "top_files": [],
        }
        result = format_evidence_panel(diag, "Test")
        assert "92%" in result
        assert "Confidence" in result

    def test_format_evidence_panel_no_confidence(self):
        diag = {
            "used_retrieval": True,
            "confidence": None,
            "evidence_files": [],
        }
        result = format_evidence_panel(diag, "Test")
        assert "Confidence" not in result


class TestFormatEvidencePanelMatchedMissedTerms:
    def test_format_evidence_panel_matched_missed_terms(self):
        diag = {
            "used_retrieval": True,
            "confidence": 0.8,
            "matched_terms": ["foo", "bar"],
            "missed_terms": ["baz"],
            "evidence_files": [],
        }
        result = format_evidence_panel(diag, "Test")
        assert "#22c55e" in result  # green for matched
        assert "foo" in result
        assert "bar" in result
        assert "#d29922" in result  # yellow for missed
        assert "baz" in result
