from django.core.management import call_command


def test_dashboard_redirects_anonymous(client):
    response = client.get("/")
    assert response.status_code == 302
    assert response.url.startswith("/accounts/login/")


def test_health_is_public(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_bootstrap_admin_does_not_replace_existing_password(
    monkeypatch, django_user_model, db
):
    monkeypatch.setenv("ADMIN_USERNAME", "owner")
    monkeypatch.setenv("ADMIN_PASSWORD", "first-secret")
    call_command("bootstrap_admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "replacement")
    call_command("bootstrap_admin")
    user = django_user_model.objects.get(username="owner")
    assert user.check_password("first-secret")
    assert not user.check_password("replacement")


def test_login_is_rate_limited_after_repeated_failures(client, db):
    for _ in range(5):
        response = client.post("/accounts/login/", {"username": "missing", "password": "wrong"})
        assert response.status_code == 200
    response = client.post("/accounts/login/", {"username": "missing", "password": "wrong"})
    assert response.status_code == 429
