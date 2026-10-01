from ptcgl_catalog.server import create_review_token, verify_review_token


def test_review_token_is_signed_and_expires():
    token = create_review_token("a-secure-test-password", "Ada", now=100)
    assert verify_review_token("a-secure-test-password", token, now=101) == "Ada"
    assert verify_review_token("wrong-password", token, now=101) is None
    assert verify_review_token("a-secure-test-password", token + "x", now=101) is None
    assert verify_review_token("a-secure-test-password", token, now=100 + 8 * 60 * 60 + 1) is None
