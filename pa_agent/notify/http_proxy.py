"""Direct HTTP transport for Feishu notifications."""
from __future__ import annotations


def feishu_post(url: str, **kwargs):
    import requests

    with requests.Session() as session:
        session.trust_env = False
        return session.post(url, **kwargs)
