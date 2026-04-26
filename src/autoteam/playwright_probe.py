"""短生命周期的 Playwright 探针子进程。

用于执行容易卡死的短时浏览器探测任务；父进程超时后可直接 kill 整个进程组，
避免 Playwright / Chromium 在后台持续堆积。
"""

import json
import logging
import sys

logging.basicConfig(level=logging.WARNING)


def _probe_team_member_count():
    from autoteam.chatgpt_api import ChatGPTTeamAPI
    from autoteam.manager import get_team_member_count

    chatgpt = ChatGPTTeamAPI()
    try:
        chatgpt.start()
        count = get_team_member_count(chatgpt)
        return {"count": count}
    finally:
        try:
            chatgpt.stop()
        except Exception:
            pass


def main():
    action = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()

    try:
        if action == "team-member-count":
            result = _probe_team_member_count()
        else:
            raise RuntimeError(f"unknown probe action: {action or 'empty'}")
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        raise SystemExit(1) from exc

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
