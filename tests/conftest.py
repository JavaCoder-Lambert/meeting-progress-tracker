import pytest
from django.contrib.auth import get_user_model


@pytest.fixture
def admin_user(db):
    return get_user_model().objects.create_superuser("owner", password="secret-pass")


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client
