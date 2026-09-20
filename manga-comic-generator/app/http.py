from __future__ import annotations

import time

import requests

RETRY_STATUS = {429, 500, 502, 503, 504}


def _wait(response, attempt: int) -> float:
    """Server-suggested wait if present, else exponential backoff."""
    try:
        return min(90.0, max(1.0, float(response.headers.get("retry-after", ""))))
    except (ValueError, TypeError):
        return min(60.0, 5.0 * 2**attempt)


def post_with_retry(url: str, attempts: int = 6, **kwargs) -> requests.Response:
    """requests.post that rides out rate limits (429), transient server errors and dropped/SSL
    connections. It never retries a quota-exhausted account (that needs a human, not a wait)."""
    for attempt in range(attempts):
        try:
            response = requests.post(url, **kwargs)
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError):
            if attempt == attempts - 1:
                raise
            time.sleep(min(30, 2 * (attempt + 1)))
            continue
        if response.status_code in RETRY_STATUS and attempt < attempts - 1 and "insufficient_quota" not in response.text:
            time.sleep(_wait(response, attempt))
            continue
        return response
    raise RuntimeError("unreachable")


def raise_for_status(response: requests.Response) -> None:
    """Like raise_for_status(), but the message includes the API's error body."""
    if not response.ok:
        raise requests.HTTPError(f"{response.status_code} from {response.url}: {response.text[:300]}", response=response)
