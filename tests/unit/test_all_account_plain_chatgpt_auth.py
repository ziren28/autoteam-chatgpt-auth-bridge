import base64
import json
import time

import pytest

from autoteam import codex_auth


def _jwt(payload):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{enc({'alg': 'none'})}.{enc(payload)}.sig"


class _FakeLocator:
    def __init__(self, visible=False):
        self.first = self
        self.last = self
        self._visible = visible

    def is_visible(self, timeout=0):
        return self._visible

    def click(self):
        return None

    def fill(self, value):
        return None

    def press(self, key):
        return None

    def inner_text(self, timeout=0):
        return ""

    def all(self):
        return []


class _FakePage:
    url = "https://chatgpt.com/"

    def __init__(self, access_token):
        self.access_token = access_token
        self.goto_urls = []

    def goto(self, url, wait_until=None, timeout=0):
        self.goto_urls.append(url)
        self.url = url

    def content(self):
        return ""

    def locator(self, selector):
        return _FakeLocator(False)

    def get_by_role(self, *args, **kwargs):
        return _FakeLocator(False)

    def evaluate(self, script, *args):
        # login_codex_via_browser 的普通登录完成后应只读取 /api/auth/session，
        # 不再进入 Codex OAuth authorization-code 页面。
        if "/api/auth/session" in script:
            return {"ok": True, "data": {"accessToken": self.access_token}}
        return ""

    def screenshot(self, *args, **kwargs):
        return None

    def close(self):
        return None


class _FakeContext:
    def __init__(self, page, session_token):
        self.page = page
        self.session_token = session_token
        self.cookies_added = []

    def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    def new_page(self):
        return self.page

    def cookies(self):
        return [
            {
                "name": "__Secure-next-auth.session-token",
                "value": self.session_token,
            }
        ]


class _FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.closed = False

    def new_context(self, **kwargs):
        return self.context

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, browser):
        self.browser = browser

    def launch(self, **kwargs):
        return self.browser


class _FakePlaywright:
    def __init__(self, browser):
        self.chromium = _FakeChromium(browser)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_login_codex_via_browser_uses_plain_chatgpt_session_for_pool_accounts(monkeypatch):
    account_id = "acc_pool_123"
    token = _jwt(
        {
            "email": "worker@example.com",
            "exp": int(time.time()) + 3600,
            "https://api.openai.com/auth": {
                "chatgpt_account_id": account_id,
                "chatgpt_plan_type": "team",
                "chatgpt_user_id": "user_1",
            },
        }
    )
    page = _FakePage(token)
    context = _FakeContext(page, "session-token-value")
    browser = _FakeBrowser(context)

    monkeypatch.setattr(codex_auth, "sync_playwright", lambda: _FakePlaywright(browser))
    monkeypatch.setattr(codex_auth, "get_playwright_launch_options", lambda: {})
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: account_id)
    monkeypatch.setattr(codex_auth, "get_chatgpt_workspace_name", lambda: "Team Workspace")
    monkeypatch.setattr(codex_auth, "_is_workspace_selection_page", lambda _page: False)
    monkeypatch.setattr(codex_auth, "_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        codex_auth,
        "_build_auth_url",
        lambda *_args, **_kwargs: pytest.fail("账号池登录不应再打开 Codex OAuth 授权页"),
    )
    monkeypatch.setattr(
        codex_auth,
        "_exchange_auth_code",
        lambda *_args, **_kwargs: pytest.fail("账号池登录不应再交换 Codex authorization code"),
    )

    bundle = codex_auth.login_codex_via_browser("worker@example.com", "", mail_client=None)

    assert bundle["email"] == "worker@example.com"
    assert bundle["account_id"] == account_id
    assert bundle["plan_type"] == "team"
    assert bundle["access_token"] == token
    assert bundle["session_token"] == "session-token-value"
    assert bundle["id_token_synthetic"] is True
    assert page.goto_urls == ["https://chatgpt.com/auth/login"]


def test_login_codex_via_browser_result_rejects_non_team_plain_session(monkeypatch):
    token = _jwt(
        {
            "email": "free@example.com",
            "exp": int(time.time()) + 3600,
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acc_free_1",
                "chatgpt_plan_type": "free",
            },
        }
    )
    page = _FakePage(token)
    context = _FakeContext(page, "session-token-value")
    browser = _FakeBrowser(context)

    monkeypatch.setattr(codex_auth, "sync_playwright", lambda: _FakePlaywright(browser))
    monkeypatch.setattr(codex_auth, "get_playwright_launch_options", lambda: {})
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "")
    monkeypatch.setattr(codex_auth, "_is_workspace_selection_page", lambda _page: False)
    monkeypatch.setattr(codex_auth, "_screenshot", lambda *args, **kwargs: None)
    monkeypatch.setattr(codex_auth.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        codex_auth,
        "_build_auth_url",
        lambda *_args, **_kwargs: pytest.fail("账号池登录不应再打开 Codex OAuth 授权页"),
    )

    result = codex_auth.login_codex_via_browser("free@example.com", "", return_result=True)

    assert result["ok"] is False
    assert result["error_type"] == "non_team_plan"
    assert "未进入 Team workspace" in result["error_detail"]
