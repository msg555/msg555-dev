import os
import requests
import time
import logging

LOGGER = logging.getLogger(__name__)


def cached_request(
    cache_key: str,
    verb: str,
    url: str,
    *args,
    **kwargs,
) -> str:
    cache_path = f"cache/{cache_key}"
    if os.path.exists(cache_path) and not os.getenv("DISABLE_CACHE"):
        with open(cache_path, "r") as fdata:
            return fdata.read()

    LOGGER.info("Querying %s %s", verb, url)

    time.sleep(1)
    resp = requests.request(verb, url, *args, **kwargs)
    resp.raise_for_status()
    with open(cache_path, "w", encoding="utf-8") as fdata:
        fdata.write(resp.text)

    return resp.text
