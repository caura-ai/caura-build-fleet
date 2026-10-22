"""REST no-content responses must not look like failed memory deletes."""

import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from requests import HTTPError, Response

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_client as mcp


@pytest.fixture(autouse=True)
def synthetic_rest_environment(monkeypatch):
    monkeypatch.setenv("MEMCLAW_API_URL", "https://example.invalid")
    monkeypatch.setenv("MEMCLAW_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("MEMCLAW_TRANSPORT", "rest")


def response(status, content=b"", content_type=None):
    result = Response()
    result.status_code = status
    result._content = content
    if content_type is not None:
        result.headers["Content-Type"] = content_type
    return result


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_rest_204_does_not_parse_json_or_retry(monkeypatch, method):
    result = response(204)
    result.json = Mock(side_effect=AssertionError("204 has no JSON body"))
    request = Mock(return_value=result)
    sleep = Mock(side_effect=AssertionError("success must not be retried"))
    monkeypatch.setattr(mcp.requests, method.lower(), request)
    monkeypatch.setattr(mcp.time, "sleep", sleep)

    assert mcp._request_with_retry(method, "memories/synthetic") == {}
    request.assert_called_once()
    result.json.assert_not_called()
    sleep.assert_not_called()


def test_manage_delete_accepts_no_content(monkeypatch):
    request = Mock(return_value=response(204))
    monkeypatch.setattr(mcp.requests, "delete", request)

    assert (
        mcp.call_tool("memclaw_manage", {"op": "delete", "memory_id": "synthetic"})
        == {}
    )
    request.assert_called_once()
    assert (
        request.call_args.args[0] == "https://example.invalid/api/v1/memories/synthetic"
    )


@pytest.mark.parametrize("status", [200, 201])
def test_other_json_success_is_preserved(monkeypatch, status):
    monkeypatch.setattr(
        mcp.requests,
        "post",
        Mock(return_value=response(status, b'{"id": "synthetic"}', "application/json")),
    )
    assert mcp._request_with_retry("POST", "memories") == {"id": "synthetic"}


def test_empty_200_still_requires_json(monkeypatch):
    monkeypatch.setattr(mcp.requests, "get", Mock(return_value=response(200)))
    with pytest.raises(ValueError, match="Expected JSON response"):
        mcp._request_with_retry("GET", "memories")


@pytest.mark.parametrize("status", [404, 500])
def test_empty_error_is_not_success(monkeypatch, status):
    request = Mock(return_value=response(status))
    monkeypatch.setattr(mcp.requests, "delete", request)
    with pytest.raises(HTTPError):
        mcp._request_with_retry("DELETE", "memories/synthetic", max_attempts=1)
    request.assert_called_once()
