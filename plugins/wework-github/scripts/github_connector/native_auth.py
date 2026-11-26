"""Wework's public localAuth command contract, using the device's gh profile."""

from . import auth_logout, browser_auth, cli

PLUGIN_KEY = "wework-github"
CONNECTOR_SLUG = "wework-github-account"


def connect():
    """Return only after authorization; Wework verifies health again afterwards."""
    current = cli.health()
    if current["status"] != "need_login":
        # Existing environment credentials work too. A network/permission error
        # must not initiate a second login or overwrite the shared account.
        return current
    # Leave room inside localAuth's 600-second command budget for the initial
    # health probe, browser startup, process cleanup and final JSON delivery.
    return browser_auth.run("login", timeout=540)


def disconnect():
    """The host's explicit disconnect action owns confirmation, not another page."""
    result = auth_logout.logout()
    if result["status"] == "ok":
        result["hint"] = (
            "所选账号已退出；设备仍有其他已保存账号连接，请刷新连接状态核对当前身份。"
            if result.get("connected")
            else "本机 GitHub CLI 账号已退出。GitHub 网站授权未撤销。"
        )
    return result


def required():
    """Return the installed plugin/connector identity for Wework's login card."""
    return {
        "error": "connector_auth_required",
        "code": "connector_auth_required",
        "pluginKey": PLUGIN_KEY,
        "connectorSlug": CONNECTOR_SLUG,
        "message": "请通过 Wework 的 GitHub CLI 账号认证连接登录，完成后从当前阻塞步骤继续。",
    }
