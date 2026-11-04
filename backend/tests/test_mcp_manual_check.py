import pytest

from app.mcp.manual_check import callback_code

ISSUER = "http://127.0.0.1:8080/realms/helpdesk"


def test_callback_accepts_matching_state_and_issuer() -> None:
    assert (
        callback_code(f"/callback?code=one-time-code&state=test-state&iss={ISSUER}", "test-state", ISSUER)
        == "one-time-code"
    )


@pytest.mark.parametrize(
    "path",
    [
        f"/callback?code=code&state=wrong&iss={ISSUER}",
        "/callback?code=code&state=test-state&iss=http://untrusted.invalid",
        f"/callback?code=first&code=second&state=test-state&iss={ISSUER}",
        f"/callback?error=denied&state=test-state&iss={ISSUER}",
        f"/callback?code=&state=test-state&iss={ISSUER}",
        f"/wrong-path?code=code&state=test-state&iss={ISSUER}",
    ],
)
def test_callback_rejects_untrusted_or_ambiguous_input(path: str) -> None:
    with pytest.raises(ValueError, match="Invalid login callback"):
        callback_code(path, "test-state", ISSUER)
