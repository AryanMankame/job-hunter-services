import pytest
from helpers import verify_correct_email_format, ResumeParsingException


class TestVerifyCorrectEmailFormat:

    @pytest.mark.parametrize("email", [
        "test@example.com",
        "first.last@company.co.in",
        "a+tag@sub.domain.org",
        "user_123@domain-name.com",
    ])
    def test_accepts_valid_emails(self, email):
        assert verify_correct_email_format(email) is True

    @pytest.mark.parametrize("email", [
        "not-an-email",
        "missing-at-sign.com",
        "@no-local-part.com",
        "no-domain@",
        "spaces in@email.com",
        "",
        "trailing-dot@domain.",
    ])
    def test_rejects_invalid_emails(self, email):
        assert verify_correct_email_format(email) is False

    def test_rejects_none_raises_typeerror(self):
        # re.match on None blows up rather than returning False -
        # documenting current behavior so a caller passing None fails loudly,
        # not silently as "invalid but no crash"
        with pytest.raises(TypeError):
            verify_correct_email_format(None)


class TestResumeParsingException:

    def test_stores_status_code_and_message(self):
        exc = ResumeParsingException(422, "bad resume")
        assert exc.status_code == 422
        assert exc.message == "bad resume"

    def test_args_tuple_matches_app_usage(self):
        # app.py reads e.args[0] / e.args[1] directly instead of
        # e.status_code / e.message - this test locks that contract so a
        # future refactor of the exception doesn't silently break app.py
        exc = ResumeParsingException(404, "not found")
        assert exc.args[0] == 404
        assert exc.args[1] == "not found"

    def test_is_a_real_exception(self):
        with pytest.raises(ResumeParsingException):
            raise ResumeParsingException(400, "boom")