from unittest.mock import MagicMock

import pytest
import requests

from app import http as http_module


def _resp(status, headers=None, text=""):
    r = MagicMock()
    r.status_code, r.headers, r.text = status, headers or {}, text
    return r


def test_429_is_retried_using_retry_after_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(http_module.time, "sleep", sleeps.append)
    post = MagicMock(side_effect=[_resp(429, {"retry-after": "7"}), _resp(503), _resp(200)])
    monkeypatch.setattr(http_module.requests, "post", post)
    assert http_module.post_with_retry("u").status_code == 200
    assert post.call_count == 3 and sleeps[0] == 7.0 and sleeps[1] == 10.0  # header, then backoff (5*2^1)


def test_quota_exhausted_429_is_not_retried(monkeypatch):
    monkeypatch.setattr(http_module.time, "sleep", lambda s: None)
    post = MagicMock(return_value=_resp(429, text='{"error": {"code": "insufficient_quota"}}'))
    monkeypatch.setattr(http_module.requests, "post", post)
    assert http_module.post_with_retry("u").status_code == 429 and post.call_count == 1


def test_gives_up_after_the_attempt_limit_and_returns_the_last_response(monkeypatch):
    monkeypatch.setattr(http_module.time, "sleep", lambda s: None)
    post = MagicMock(return_value=_resp(429))
    monkeypatch.setattr(http_module.requests, "post", post)
    assert http_module.post_with_retry("u", attempts=3).status_code == 429 and post.call_count == 3


def test_connection_errors_retry_and_client_errors_do_not(monkeypatch):
    monkeypatch.setattr(http_module.time, "sleep", lambda s: None)
    post = MagicMock(side_effect=[requests.exceptions.SSLError("x"), _resp(400)])
    monkeypatch.setattr(http_module.requests, "post", post)
    assert http_module.post_with_retry("u").status_code == 400 and post.call_count == 2


def test_error_message_includes_the_response_body():
    r = MagicMock()
    r.ok, r.status_code, r.url, r.text = False, 429, "https://api.x/y", "rate limit exceeded: 5 images/min"
    with pytest.raises(requests.HTTPError, match="rate limit exceeded"):
        http_module.raise_for_status(r)
    ok = MagicMock()
    ok.ok = True
    http_module.raise_for_status(ok)
