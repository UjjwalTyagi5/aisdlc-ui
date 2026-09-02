from shared.eval.import_screening import scan_for_credentials


def test_clean_text_returns_empty():
    assert scan_for_credentials("Cover acceptance criteria and scope.") == []


def test_github_pat_detected():
    hits = scan_for_credentials("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    assert hits


def test_bearer_token_detected():
    hits = scan_for_credentials("Authorization: Bearer abcdefghijklmnop")
    assert hits


def test_password_assignment_detected():
    hits = scan_for_credentials("password=supersecret123")
    assert hits


def test_api_key_detected():
    hits = scan_for_credentials("key: sk-abcdefghijklmnopqrstuvwxyz0123456789")
    assert hits


def test_url_credentials_detected():
    hits = scan_for_credentials("https://user:tokentokentoken@github.com/org/repo.git")
    assert hits


def test_never_echoes_the_matched_secret():
    hits = scan_for_credentials("token: ghp_abcdefghijklmnopqrstuvwxyz0123456789")
    joined = " ".join(hits)
    assert "ghp_abcdefghijklmnopqrstuvwxyz0123456789" not in joined


def test_multiple_categories_all_reported():
    hits = scan_for_credentials(
        "token: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n"
        "Authorization: Bearer abcdefghijklmnop"
    )
    assert len(hits) >= 2
