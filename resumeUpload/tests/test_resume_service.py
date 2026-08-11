import pytest
from unittest.mock import Mock
from ResumeService import ResumeService
from .conftest import make_blank_pdf_bytes


class TestGetTextFromPdf:

    def test_returns_empty_string_for_blank_page_pdf(self):
        # This is the real-world "scanned image PDF" case - pypdf parses it
        # fine structurally but extracts zero text. app.py depends on this
        # returning "" (not raising) so it can turn it into a clean 422.
        service = ResumeService()
        result = service.get_text_from_pdf(make_blank_pdf_bytes())
        assert result == ""

    def test_raises_on_garbage_bytes(self):
        # Not a PDF at all - pypdf should fail to even parse the structure.
        # This currently propagates as a raw pypdf exception; app.py's
        # outer `except Exception` catches it, but it's worth knowing
        # exactly what type surfaces here in case you want to catch it
        # more specifically later.
        service = ResumeService()
        with pytest.raises(Exception):
            service.get_text_from_pdf(b"this is not a pdf file at all")

    def test_raises_on_empty_bytes(self):
        service = ResumeService()
        with pytest.raises(Exception):
            service.get_text_from_pdf(b"")


class TestParseResume:

    def test_delegates_to_parser_and_returns_its_result(self):
        service = ResumeService()
        mock_parser = Mock()
        mock_parser.parse.return_value = "parsed-result-sentinel"

        result = service.parse_resume(mock_parser, "some resume text")

        mock_parser.parse.assert_called_once_with("some resume text")
        assert result == "parsed-result-sentinel"

    def test_propagates_parser_exceptions(self):
        # confirms ResumeService doesn't swallow LLM/parsing failures -
        # app.py relies on this bubbling up to its own try/except
        service = ResumeService()
        mock_parser = Mock()
        mock_parser.parse.side_effect = ValueError("model returned invalid JSON")

        with pytest.raises(ValueError, match="invalid JSON"):
            service.parse_resume(mock_parser, "some text")