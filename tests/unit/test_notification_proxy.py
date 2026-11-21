from pa_agent.notify.http_proxy import feishu_post


def test_feishu_bypasses_inherited_proxy(monkeypatch):
    import os
    import requests

    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890 ")
    captured = {}

    def send(session, request, **kwargs):
        captured.update(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"code":0}'
        return response

    monkeypatch.setattr(requests.Session, "send", send)
    response = feishu_post("https://open.feishu.cn/test", json={"test": True}, timeout=10)
    assert response.json() == {"code": 0}
    assert captured["proxies"] == {}
    assert captured["timeout"] == 10
    assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:7890 "
