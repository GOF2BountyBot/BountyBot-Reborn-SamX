"""Custom assertion helpers for tests."""


def assert_valid_api_response(response, expected_status=200):
    """Assert that an API response is valid."""
    assert response.status_code == expected_status, (
        f"Expected status {expected_status}, got {response.status_code}: {response.text}"
    )


def assert_json_structure(data, required_keys):
    """Assert that a JSON dict contains all required keys."""
    missing = set(required_keys) - set(data.keys())
    assert not missing, f"Missing keys in response: {missing}"


def assert_pagination_response(data):
    """Assert that a paginated response has the expected structure."""
    required = ["items", "total", "page", "page_size"]
    assert_json_structure(data, required)
    assert isinstance(data["items"], list)
    assert isinstance(data["total"], int)
