#!/usr/bin/env python3
"""End-to-end check against a real, isolated agent desktop. Run: python tests/smoke.py

Starts a second desktop with its own runtime and data directories, so a running desktop is left alone.
Typing is checked byte-for-byte through a terminal writing to a file; pointer and key events are checked
through a Chromium page that reports the events it receives in its window title. Missing apps are skipped.
"""

import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import time
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(tempfile.mkdtemp(prefix="agent-desktop-smoke-"))
os.environ.update(AGENT_DESKTOP_RUNTIME=str(WORK / "run"), AGENT_DESKTOP_DATA=str(WORK / "data"),
                  AGENT_DESKTOP_RESOLUTION="1024x640")
os.environ.pop("AGENT_DESKTOP_ALLOW_HOST_CLIPBOARD", None)
loader = SourceFileLoader("agent_desktop", str(ROOT / "bin/agent-desktop"))
ad = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
loader.exec_module(ad)

TEXT = "Hello World $HOME ~`!@#$%^&*()_+-=[]{}\\|;:'\",.<>/?\n\tåäö ÅÄÖ é € λ 日本 😀 End\nplain\n"
PAGE = """data:text/html,<title>ready</title><body style="margin:0;width:3000px;height:3000px"><script>
const log = []; const put = s => { log.push(s); document.title = log.slice(-4).join(' ') };
const mods = e => (e.ctrlKey ? 'C' : '') + (e.shiftKey ? 'S' : '') + (e.altKey ? 'A' : '');
addEventListener('mousedown', e => put('down' + e.button + mods(e) + ':' + e.detail));
addEventListener('mouseup', e => put('up' + e.button));
addEventListener('wheel', e => put('wheel' + Math.sign(e.deltaX) + ',' + Math.sign(e.deltaY)), {passive: true});
addEventListener('keydown', e => { if (!['Control', 'Shift', 'Alt'].includes(e.key)) { put('key' + mods(e) + e.key); e.preventDefault() } });
addEventListener('contextmenu', e => e.preventDefault());
</script>""".replace("\n", "").replace("#", "%23")
failures = []


def check(name, condition, detail=""):
    print(f"{'ok  ' if condition else 'FAIL'} {name}{'' if condition else f': {detail}'}")
    if not condition:
        failures.append(name)


def png_size(data):
    return struct.unpack(">II", data[16:24])


def title_after(rfb, actions, window_id):
    ad.run_actions(rfb, actions)
    time.sleep(0.3)
    return next(w["title"] for w in ad.windows() if w["id"] == window_id)


def main():
    print(ad.start())
    data = ad.state()
    token = data["token"]
    rfb = ad.Rfb()
    check("resolution comes from AGENT_DESKTOP_RESOLUTION", (rfb.width, rfb.height) == (1024, 640))
    rfb.capture()
    check("screenshot is a full-size PNG", png_size(rfb.png()) == (1024, 640))
    check("zoom crops and enlarges", png_size(rfb.png((10, 20, 100, 50), 3)) == (300, 150))

    try:
        ad.run_actions(rfb, [{"type": "click", "x": 1, "y": 1}, {"type": "click", "x": 5000, "y": 1}])
        check("out-of-range batch is rejected", False)
    except RuntimeError as exc:
        check("out-of-range batch is rejected before any input", "x must be" in str(exc), str(exc))

    terminal = next((name for name in ("foot", "kitty", "alacritty") if shutil.which(name)), None)
    if terminal:
        out = WORK / "typed.txt"
        opened = ad.launch([terminal, *(["-e"] if terminal != "foot" else []), "sh", "-c", f"cat > {out}"])
        check("launch reports the new window", opened["window"] is not None, opened)
        time.sleep(0.5)
        ad.run_actions(rfb, [{"type": "type", "text": TEXT}, {"type": "key", "key": "ctrl+d"}])
        time.sleep(0.5)
        got = out.read_text() if out.exists() else ""
        check("typing is exact, including shifted symbols and Unicode", got == TEXT, repr(got))
    else:
        print("skip terminal typing checks: no foot, kitty, or alacritty")

    ad.clipboard("private", "write", "clip ✓")
    check("private clipboard round trip", ad.clipboard("private", "read") == "clip ✓")
    try:
        ad.clipboard("host", "read")
        check("host clipboard read is gated", False)
    except RuntimeError as exc:
        check("host clipboard read is gated", "disabled" in str(exc), str(exc))

    browser = next((name for name in ("chromium", "google-chrome-stable", "brave") if shutil.which(name)), None)
    if browser:
        opened = ad.launch([browser, f"--user-data-dir={WORK / 'browser'}", "--no-first-run", "--no-default-browser-check",
                            "--password-store=basic", "--new-window", PAGE], 15000)
        window = opened["window"]
        check("browser window opened", window is not None, opened)
        if window:
            wid = window["id"]
            deadline = time.monotonic() + 10
            while "ready" not in next(w["title"] for w in ad.windows() if w["id"] == wid) and time.monotonic() < deadline:
                time.sleep(0.2)
            cases = [
                ("click", [{"type": "click", "x": 500, "y": 400}], "down0:1 up0"),
                ("double click", [{"type": "double_click", "x": 500, "y": 400}], "down0:2 up0"),
                ("triple click", [{"type": "click", "x": 500, "y": 400, "count": 3}], "down0:3 up0"),
                ("ctrl+shift click", [{"type": "click", "x": 500, "y": 400, "modifiers": ["ctrl", "shift"]}], "down0CS:1 up0"),
                ("right click", [{"type": "click", "x": 500, "y": 400, "button": "right"}], "down2:1 up2"),
                ("mouse down/up", [{"type": "mouse_down", "x": 500, "y": 400}, {"type": "mouse_up"}], "down0:1 up0"),
                ("vertical scroll", [{"type": "scroll", "x": 500, "y": 400, "steps": 1}], "wheel0,1"),
                ("horizontal scroll", [{"type": "scroll", "x": 500, "y": 400, "horizontal_steps": -1}], "wheel-1,0"),
                ("key combo", [{"type": "key", "key": "ctrl+shift+k"}], "keyCSK"),
                ("shifted key", [{"type": "key", "key": "$"}], "keyS$"),
                ("key held across actions", [{"type": "key_down", "key": "ctrl"}, {"type": "key", "key": "b"}], "keyCb"),
                ("held keys released after batch", [{"type": "key", "key": "b"}], "keyb"),
                ("drag", [{"type": "drag", "path": [{"x": 200, "y": 300}, {"x": 400, "y": 350}], "duration_ms": 100}], "down0:1 up0"),
            ]
            for index, (name, actions, expected) in enumerate(cases):
                for action in actions:  # A new spot per case, so the OS does not merge clicks into multi-clicks.
                    if "x" in action:
                        action["x"] += index * 30
                title = title_after(rfb, actions, wid)
                check(name, title.split(" - ")[0].endswith(expected), f"title {title!r}, wanted ...{expected!r}")
            ad.window_action("close", wid)
    else:
        print("skip pointer checks: no chromium")

    listed = ad.windows()
    if listed:
        ad.window_action("focus", listed[0]["id"])
        check("focus window by id", ad.focused_window()["id"] == listed[0]["id"])
    rfb.close()
    print(ad.stop())
    time.sleep(0.2)
    leftover = ad.marked_processes(token)
    check("stop ends every process from the session", not leftover, leftover)
    check("stop removes runtime files", not ad.STATE.exists() and not ad.VNC.exists())


if __name__ == "__main__":
    try:
        main()
    finally:
        ad.stop()
        shutil.rmtree(WORK, ignore_errors=True)
    print(f"\n{len(failures)} failed" if failures else "\nall checks passed")
    sys.exit(1 if failures else 0)
