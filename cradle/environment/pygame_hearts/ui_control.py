"""UI control for I'll Cop Your Heart pygame game."""

import os
import subprocess
import tempfile
import time

from PIL import Image

from cradle.config import Config
from cradle.log import Logger
from cradle.environment import UIControl

config = Config()
logger = Logger()

WINDOW_TITLE = "I'll Cop Your Heart"

_XENV = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":0")}

# Cache (wid, left, top, width, height) so we only call xdotool once per session.
_win_cache = None  # (wid_str, x, y, w, h)


def _find_game_window(force=False):
    """Return (wid, x, y, w, h) for the pygame window, cached after first call."""
    global _win_cache
    if _win_cache is not None and not force:
        return _win_cache
    try:
        out = subprocess.check_output(
            ["xdotool", "search", "--name", WINDOW_TITLE],
            stderr=subprocess.DEVNULL, env=_XENV,
        ).decode().strip()
        if not out:
            return _win_cache
        wid = out.splitlines()[0].strip()
        geo = subprocess.check_output(
            ["xdotool", "getwindowgeometry", "--shell", wid],
            stderr=subprocess.DEVNULL, env=_XENV,
        ).decode()
        vals = {}
        for line in geo.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = int(v.strip())
        _win_cache = (wid,
                      vals.get("X", 0), vals.get("Y", 0),
                      vals.get("WIDTH", 600), vals.get("HEIGHT", 600))
        return _win_cache
    except Exception as e:
        logger.warn(f"Could not find game window: {e}")
        return _win_cache


def _screenshot_xwd(wid, dest_png):
    """
    Capture the game window via the X11 backing store (xwd + ImageMagick).
    Works for XWayland apps; ~0.2 s vs ~1.9 s for gnome-screenshot.
    Returns True on success.
    """
    try:
        xwd_data = subprocess.check_output(
            ["xwd", "-id", wid, "-silent"],
            stderr=subprocess.DEVNULL, env=_XENV, timeout=3,
        )
        if not xwd_data:
            return False
        r = subprocess.run(
            ["convert", "xwd:-", f"png:{dest_png}"],
            input=xwd_data, capture_output=True, timeout=5,
        )
        return (r.returncode == 0
                and os.path.exists(dest_png)
                and os.path.getsize(dest_png) > 0)
    except Exception:
        return False


class PyGameHeartsUIControl(UIControl):
    """Minimal UI control for the pygame hearts game."""

    def pause_game(self, env_name, ide_name):
        pass

    def unpause_game(self, env_name, ide_name):
        pass

    def switch_to_game(self, env_name, ide_name):
        # Use wmctrl to raise the window — xdotool windowfocus sends synthetic
        # events that pygame misinterprets as arrow key presses.
        try:
            subprocess.run(
                ["wmctrl", "-a", WINDOW_TITLE],
                capture_output=True, timeout=3,
            )
            time.sleep(0.2)
        except Exception as e:
            logger.warn(f"Could not raise game window: {e}")

    def exit_back_to_pause(self, env_name, ide_name):
        pass

    def exit_back_to_game(self, env_name, ide_name):
        pass

    def is_env_paused(self):
        return False

    def take_screenshot(self, tid: float, screen_region=None) -> str:
        output_path = os.path.join(config.work_dir, f"screen_{tid}.png")

        # Detect (and cache) the game window ID + position.
        win = _find_game_window()
        if win:
            wid, left, top, width, height = win
        else:
            wid = None
            if screen_region is None:
                screen_region = config.env_region
            left, top, width, height = screen_region

        t0 = time.time()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            ok = False
            if wid:
                # Primary: xwd reads the XWayland X11 backing store directly.
                # ~0.23 s vs ~1.9 s for gnome-screenshot.
                ok = _screenshot_xwd(wid, tmp_path)

            if ok:
                img = Image.open(tmp_path).convert("RGB")
                img.save(output_path)
                print(f"  [screenshot] xwd ({img.size[0]}x{img.size[1]}) in {time.time()-t0:.2f}s")
            else:
                # Fallback: gnome-screenshot full screen + crop.
                r = subprocess.run(
                    ["gnome-screenshot", "-f", tmp_path, "--delay=0"],
                    env=os.environ,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                )
                if r.returncode != 0 or not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                    raise RuntimeError("All screenshot methods failed")
                img = Image.open(tmp_path).convert("RGB")
                cropped = img.crop((left, top, left + width, top + height))
                cropped.save(output_path)
                print(f"  [screenshot] gnome fallback ({cropped.size[0]}x{cropped.size[1]}) in {time.time()-t0:.2f}s")
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return output_path
