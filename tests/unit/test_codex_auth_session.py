from autoteam import codex_auth


def test_login_codex_via_session_uses_chatgpt_session_bundle_without_oauth(monkeypatch):
    calls = []

    class FakeChatGPTTeamAPI:
        def __init__(self):
            self.access_token = "access-token"
            self.workspace_name = "Idapro"

        def start_with_session(self, session_token, account_id, workspace_name, require_browser=False):
            calls.append(("start_with_session", session_token, account_id, workspace_name, require_browser))

        def _fetch_access_token(self, allow_bearer_file=True):
            calls.append(("fetch_access_token", allow_bearer_file))
            return "session"

        def stop(self):
            calls.append(("stop",))

    def fake_convert(info):
        calls.append(("convert", info))
        return {"email": info["email"], "account_id": info["account_id"], "plan_type": "team"}

    monkeypatch.setattr(codex_auth, "ChatGPTTeamAPI", FakeChatGPTTeamAPI)
    monkeypatch.setattr(codex_auth, "chatgpt_session_to_auth_bundle", fake_convert)
    monkeypatch.setattr(codex_auth, "_exchange_auth_code", lambda *a, **k: (_ for _ in ()).throw(AssertionError("OAuth exchange must not run")))
    monkeypatch.setattr(codex_auth, "get_admin_email", lambda: "owner@example.com")
    monkeypatch.setattr(codex_auth, "get_admin_session_token", lambda: "session-token")
    monkeypatch.setattr(codex_auth, "get_chatgpt_account_id", lambda: "acc-1")
    monkeypatch.setattr(codex_auth, "get_chatgpt_workspace_name", lambda: "Idapro")

    bundle = codex_auth.login_codex_via_session()

    assert bundle == {"email": "owner@example.com", "account_id": "acc-1", "plan_type": "team"}
    assert calls[0] == ("start_with_session", "session-token", "acc-1", "Idapro", True)
    assert calls[1] == ("fetch_access_token", False)
    assert calls[2][0] == "convert"
    assert calls[2][1]["access_token"] == "access-token"
    assert calls[2][1]["session_token"] == "session-token"
    assert calls[-1] == ("stop",)


def test_session_codex_auth_flow_start_completes_from_plain_session_without_auth_url(monkeypatch):
    calls = []

    class FakeChatGPTTeamAPI:
        def __init__(self):
            self.access_token = "access-token"
            self.workspace_name = "Idapro"

        def start_with_session(self, session_token, account_id, workspace_name, require_browser=False):
            calls.append(("start_with_session", session_token, account_id, workspace_name, require_browser))

        def _fetch_access_token(self, allow_bearer_file=True):
            calls.append(("fetch_access_token", allow_bearer_file))
            return "session"

        def stop(self):
            calls.append(("stop",))

    monkeypatch.setattr(codex_auth, "ChatGPTTeamAPI", FakeChatGPTTeamAPI)
    monkeypatch.setattr(codex_auth, "chatgpt_session_to_auth_bundle", lambda info: {**info, "plan_type": "team"})
    monkeypatch.setattr(codex_auth, "_exchange_auth_code", lambda *a, **k: (_ for _ in ()).throw(AssertionError("OAuth exchange must not run")))

    flow = codex_auth.SessionCodexAuthFlow(
        email="owner@example.com",
        session_token="session-token",
        account_id="acc-1",
        workspace_name="Idapro",
        auth_file_callback=lambda bundle: "/tmp/auth.json",
    )

    result = flow.start()
    info = flow.complete()
    flow.stop()

    assert result == {"step": "completed", "detail": "plain_chatgpt_session"}
    assert info["auth_file"] == "/tmp/auth.json"
    assert info["bundle"]["access_token"] == "access-token"
    assert calls[0] == ("start_with_session", "session-token", "acc-1", "Idapro", True)
    assert calls[1] == ("fetch_access_token", False)
    assert calls[-1] == ("stop",)


def test_refresh_main_auth_file_saves_bundle_from_session_login(monkeypatch):
    monkeypatch.setattr(
        codex_auth,
        "login_codex_via_session",
        lambda: {"email": "owner@example.com", "account_id": "acc-1", "plan_type": "team"},
    )
    monkeypatch.setattr(codex_auth, "save_main_auth_file", lambda bundle: f"/tmp/{bundle['account_id']}.json")

    result = codex_auth.refresh_main_auth_file()

    assert result == {
        "email": "owner@example.com",
        "auth_file": "/tmp/acc-1.json",
        "plan_type": "team",
    }


class _FakeElement:
    def __init__(self, text):
        self._text = text
        self.clicked = False

    def is_visible(self, timeout=0):
        return True

    def inner_text(self, timeout=0):
        return self._text

    def click(self, timeout=0, force=False):
        self.clicked = True


class _FakeCollection:
    def __init__(self, items=None, text=None):
        self._items = items or []
        self._text = text

    def all(self):
        return list(self._items)

    def inner_text(self, timeout=0):
        if self._text is None:
            raise AssertionError("unexpected inner_text call")
        return self._text


class _FakePage:
    def __init__(self, *, url, body, elements=None):
        self.url = url
        self._body = body
        self._elements = elements or []

    def locator(self, selector):
        if selector == "body":
            return _FakeCollection(text=self._body)
        return _FakeCollection(items=self._elements)


def test_workspace_selection_detection_ignores_otp_pages():
    page = _FakePage(
        url="https://auth.openai.com/email-verification",
        body="Check your inbox Enter the verification code we just sent to user@example.com",
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_team_workspace(page, "Idapro") is False


def test_workspace_label_candidates_ignore_action_buttons():
    items = [
        _FakeElement("Cancel"),
        _FakeElement("Log in with a one-time code"),
        _FakeElement("Idapro"),
        _FakeElement("Personal account"),
    ]
    page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Idapro Personal account",
        elements=items,
    )

    candidates = [text for text, _loc in codex_auth._workspace_label_candidates(page)]

    assert candidates == ["Idapro", "Personal account"]


def test_workspace_selection_detection_ignores_generic_organization_setup_page():
    page = _FakePage(
        url="https://auth.openai.com/organization",
        body="New organization Finish setting up on the next page",
        elements=[_FakeElement("New organization Finish setting up on the next page")],
    )

    assert codex_auth._is_workspace_selection_page(page) is False
    assert codex_auth._select_team_workspace(page, "Idapro") is False


def test_team_workspace_selection_requires_exact_workspace_name():
    items = [
        _FakeElement("New organization Finish setting up on the next page"),
        _FakeElement("Personal account"),
    ]
    page = _FakePage(
        url="https://auth.openai.com/workspace",
        body="Choose a workspace Workspace Personal account",
        elements=items,
    )

    assert codex_auth._workspace_label_candidates(page) == [("Personal account", items[1])]
    assert codex_auth._select_team_workspace(page, "Idapro") is False
