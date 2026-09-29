from django.urls import reverse
import pytest


def test_health_check_endpoint(client, db):
    url = reverse("core:health_check")
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["components"]["database"] == "healthy"
