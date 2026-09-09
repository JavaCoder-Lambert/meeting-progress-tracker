import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_login_failures():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_admin_and_account_login_share_failure_limit(client):
    for path in ["/accounts/login/", "/admin/login/", "/accounts/login/", "/admin/login/", "/admin/login/"]:
        assert client.post(path, {"username": "missing", "password": "wrong"}).status_code == 200
    for path in ["/accounts/login/", "/admin/login/"]:
        assert client.post(path, {"username": "missing", "password": "wrong"}).status_code == 429


@pytest.mark.django_db
def test_trusted_caddy_keeps_real_client_failure_budgets_separate(client, settings):
    settings.LOGIN_TRUSTED_PROXY_CIDRS = ["172.30.7.2/32"]
    proxy = {"REMOTE_ADDR": "172.30.7.2", "HTTP_X_TRACKER_CLIENT_IP": "203.0.113.10"}
    for _ in range(5):
        client.post("/admin/login/", {"username": "missing", "password": "wrong"}, **proxy)
    assert client.post("/accounts/login/", {}, **proxy).status_code == 429
    proxy["HTTP_X_TRACKER_CLIENT_IP"] = "203.0.113.11"
    assert client.post("/admin/login/", {}, **proxy).status_code == 200


@pytest.mark.parametrize("peer,header", [
    ("203.0.113.12", "203.0.113.90"),
    ("172.30.7.2", "203.0.113.90, 203.0.113.91"),
    ("172.30.7.2", "malformed"),
])
@pytest.mark.django_db
def test_forged_or_multivalue_headers_cannot_reset_failure_budget(client, settings, peer, header):
    settings.LOGIN_TRUSTED_PROXY_CIDRS = ["172.30.7.2/32"]
    for number in range(5):
        client.post("/accounts/login/", {}, REMOTE_ADDR=peer,
                    HTTP_X_TRACKER_CLIENT_IP=header, HTTP_X_FORWARDED_FOR=f"198.51.100.{number}")
    assert client.post("/admin/login/", {}, REMOTE_ADDR=peer,
                       HTTP_X_TRACKER_CLIENT_IP=header + "x", HTTP_X_FORWARDED_FOR="192.0.2.1").status_code == 429


@pytest.mark.parametrize("path", ["/admin/login/", "/accounts/login/"])
def test_successful_login_clears_shared_failure_budget(client, admin_user, path):
    for _ in range(4):
        client.post("/accounts/login/", {"username": "owner", "password": "wrong"})
    response = client.post(path, {"username": "owner", "password": "secret-pass"})
    assert response.status_code == 302
    client.logout()
    for _ in range(5):
        assert client.post("/admin/login/", {"username": "owner", "password": "wrong"}).status_code == 200
    assert client.post("/accounts/login/", {}).status_code == 429


@pytest.mark.django_db
def test_admin_login_still_rejects_non_staff_user(client, django_user_model):
    django_user_model.objects.create_user("regular", password="secret-pass")
    assert client.post("/admin/login/", {"username": "regular", "password": "secret-pass"}).status_code == 200
    assert client.get("/admin/").status_code == 302


def test_caddy_overwrites_client_address_and_compose_trusts_only_caddy():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "header_up X-Tracker-Client-IP {remote_host}" in (root / "docker/Caddyfile").read_text()
    compose = (root / "docker-compose.server.yml").read_text()
    assert "LOGIN_TRUSTED_PROXY_CIDRS: ${TRACKER_PROXY_IP:-172.30.7.2}/32" in compose
    assert "ipv4_address: ${TRACKER_PROXY_IP:-172.30.7.2}" in compose
