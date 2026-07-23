import httpx

from app.delivery.emailer import _send_telegram_message


def test_send_telegram_message_retries_transport_errors(monkeypatch) -> None:
    attempts = []

    def fake_post(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ProxyError("temporary proxy failure")
        return httpx.Response(200, request=httpx.Request("POST", args[0]))

    monkeypatch.setattr("app.delivery.emailer.httpx.post", fake_post)
    monkeypatch.setattr("app.delivery.emailer.time.sleep", lambda _: None)
    monkeypatch.setattr("app.delivery.emailer.settings.telegram_bot_token", "token")

    _send_telegram_message("chat", "message")

    assert len(attempts) == 3
