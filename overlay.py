from pathlib import Path

from config import MARC_DEBUG

TEMPLATE_ROOT = Path(__file__).parent / "templates"
OVERLAY_TEMPLATE = (TEMPLATE_ROOT / "overlay.html").read_text(encoding="utf-8")
LANDING = (TEMPLATE_ROOT / "landing.html").read_text(encoding="utf-8")


def overlay_script(session_id: str, server_base: str, target_url: str) -> str:
    payload = (
        OVERLAY_TEMPLATE
        .replace("__SESSION_ID__", session_id)
        .replace("__SERVER_BASE__", server_base)
        .replace("__TARGET_URL__", target_url)
    )
    debug_flag = "true" if MARC_DEBUG else "false"
    return payload.replace("__MARC_DEBUG__", debug_flag)
