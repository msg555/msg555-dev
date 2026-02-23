import logging
import os
import time

import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


def cached_request(
    cache_key: str,
    verb: str,
    url: str,
    *args,
    force: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs,
) -> str:
    cache_path = f"cache/{cache_key}"
    if not force and os.path.exists(cache_path) and not os.getenv("DISABLE_CACHE"):
        with open(cache_path, "r", encoding="utf-8") as fdata:
            return fdata.read()

    LOGGER.info("Querying %s %s", verb, url)

    time.sleep(1)
    resp = requests.request(verb, url, *args, timeout=timeout, **kwargs)
    resp.raise_for_status()
    with open(cache_path, "w", encoding="utf-8") as fdata:
        fdata.write(resp.text)

    return resp.text
