import base64
import json
import time
from pathlib import Path

from autoteam import api
from autoteam import codex_auth


def _b64url_json(payload):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_convert_chatgpt_session_to_cpa_auth_bundle_builds_synthetic_id_token():
    expires = time.time() + 3600
    bundle = codex_auth.chatgpt_session_to_auth_bundle(
        {
            "email": "owner@example.com",
            "access_token": "chatgpt-access-token",
            "session_token": "session-token",
            "account_id": "acc-1",
            "plan_type": "team",
            "expired": expires,
        }
    )

    assert bundle["email"] == "owner@example.com"
    assert bundle["access_token"] == "chatgpt-access-token"
    assert bundle["refresh_token"] == ""
    assert bundle["account_id"] == "acc-1"
    assert bundle["plan_type"] == "team"
    assert bundle["session_token"] == "session-token"
    header, payload, sig = bundle["id_token"].split(".")
    assert sig == ""
    decoded_payload = json.loads(base64.urlsafe_b64decode(payload + "=="))
    assert decoded_payload["email"] == "owner@example.com"
    assert decoded_payload["https://api.openai.com/auth"] == {
        "chatgpt_account_id": "acc-1",
        "chatgpt_plan_type": "team",
    }


def test_finish_admin_login_creates_main_auth_file_from_plain_chatgpt_session(monkeypatch, tmp_path):
    auth_dir = tmp_path / "auths"
    monkeypatch.setattr(codex_auth, "AUTH_DIR", auth_dir)
    monkeypatch.setattr("autoteam.auth_storage.AUTH_DIR", auth_dir)
    monkeypatch.setattr(api, "_auto_sync_main_auth_after_admin_login", True, raising=False)
    synced = []
    monkeypatch.setattr(
        "autoteam.sync_targets.sync_main_codex_to_configured_targets",
        lambda auth_file: synced.append(auth_file),
    )

    class FakeAdminLoginAPI:
        access_token = "chatgpt-access-token"

        def complete_admin_login(self):
            return {
                "email": "owner@example.com",
                "session_token": "session-token",
                "account_id": "acc-1",
                "workspace_name": "Team Workspace",
            }

        def stop(self):
            pass

    monkeypatch.setattr(api, "_admin_login_api", FakeAdminLoginAPI())
    monkeypatch.setattr(api, "_admin_login_step", "code_required")
    monkeypatch.setattr(api._pw_executor, "run", lambda func, *args, **kwargs: func(*args, **kwargs))

    result = api._finish_admin_login({"step": "completed"})

    info = result["info"]
    assert info["main_auth_file"]
    assert Path(info["main_auth_file"]).exists()
    assert synced == [info["main_auth_file"]]
    data = json.loads(Path(info["main_auth_file"]).read_text())
    assert data["type"] == "codex"
    assert data["email"] == "owner@example.com"
    assert data["access_token"] == "chatgpt-access-token"
    assert data["account_id"] == "acc-1"
