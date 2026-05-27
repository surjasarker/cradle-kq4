"""
Smoke test for Simon Says skills.

Run this with the Simon Says window (Game.py) already open and visible.
The script will:
  A. Try every skill through the production path the executor uses
     (skill_registry.execute_skill) — this is what the agent does.
  B. Try the skill functions directly with gm passed in, to isolate
     whether the failures are wiring or implementation.
  C. Probe the actual tkinter window: print its position and size, and
     attempt a click at the geometric center of each colored button.

Usage:
    1. Open the Simon Says window first:
           python Game.py
    2. In another terminal, with the cradle conda env active:
           python test_simon_says_skills.py
    3. Watch what happens. Don't move the mouse during the test.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Note: we deliberately do NOT import cradle.environment.simon_says.skill_registry
# at module top. It reads conf/simon_says_buttons.json at import time, so we run
# the color-based calibration first and only import the registry afterwards.


PASS = "PASS"
FAIL = "FAIL"
results = []  # list of (section, name, status, detail)


def banner(msg):
    print()
    print("=" * 72)
    print(msg)
    print("=" * 72)


def record(section, name, ok, detail=""):
    status = PASS if ok else FAIL
    results.append((section, name, status, detail))
    print(f"  [{status}] {name}" + (f"  -- {detail}" if detail else ""))


def try_call(label, fn, *args, **kwargs):
    """Call `fn(*args, **kwargs)`, print result/exception, return (ok, detail)."""
    try:
        result = fn(*args, **kwargs)
        return True, f"result={result!r}"
    except Exception as e:
        traceback.print_exc()
        return False, f"{type(e).__name__}: {e}"


def section_a_production_path(skill_registry):
    """How the executor invokes skills."""
    banner("A. Production path: skill_registry.execute_skill(...)")
    print("This is exactly how the agent's actions reach the game.")
    print("If these fail, the model's output never moves the mouse.\n")

    cases = [
        ("wait(0.3)",                       "wait",       {"duration": 0.3}),
        ("screenshot()",                    "screenshot", {}),
        ("click(color='red')",              "click",      {"color": "red"}),
    ]
    for label, name, params in cases:
        print(f"\n> {label}")
        try:
            resp = skill_registry.execute_skill(skill_name=name, skill_params=params)
            ok = bool(resp) or resp is None
            record("A", label, ok, f"returned {resp!r}")
        except Exception as e:
            traceback.print_exc()
            record("A", label, False, f"{type(e).__name__}: {e}")
        time.sleep(0.5)


def section_b_skills_direct():
    """Call the skill bodies directly to catch bugs the registry path hides."""
    banner("B. Direct skill calls (new signatures, no gm)")
    print("Skills now use module-level io_env / lifecycle helpers, no gm needed.\n")

    print("> Wait.execute(duration=0.3)")
    ok, detail = try_call("wait", Wait.execute, duration=0.3)
    record("B", "Wait.execute", ok, detail)

    print("\n> TakeScreenshot.execute()")
    ok, detail = try_call("screenshot", TakeScreenshot.execute)
    record("B", "TakeScreenshot.execute", ok, detail)

    for color in ("red", "blue", "yellow", "green"):
        print(f"\n> BasicClick.execute(color='{color}')")
        ok, detail = try_call("click", BasicClick.execute, color=color)
        record("B", f"BasicClick.execute({color})", ok, detail)
        time.sleep(1.5)


def section_c_real_window():
    """Find button centers by color, write calibration, and click each.

    Window geometry alone can't tell us where the tkinter buttons are
    because tkinter sizes Button widgets in character units (font-dependent),
    not in pixel fractions. Instead we screenshot the screen, find pixels
    matching each pure color, and take the centroid of the largest blob.

    This MUST run before importing the skill registry, because the registry
    reads conf/simon_says_buttons.json at import time.
    """
    banner("C. Color-based button calibration (runs first)")
    print("Locates buttons by scanning the screenshot for their fill color,")
    print("writes conf/simon_says_buttons.json (read by the skill registry),")
    print("and clicks each found center with pyautogui (bypassing AHK).\n")

    import pyautogui
    import numpy as np

    # Take a screenshot of the whole primary monitor
    shot = pyautogui.screenshot()
    arr = np.array(shot)  # shape: (H, W, 3) RGB
    H, W = arr.shape[:2]
    print(f"  Screenshot: {W}x{H}")

    # Exact tkinter fill colors used in Game.py
    targets = {
        "red":    (255,   0,   0),
        "blue":   (  0,   0, 255),
        "yellow": (255, 255,   0),
        "green":  (  0, 128,   0),  # tk 'green' = (0,128,0), NOT (0,255,0)!
    }

    # The "START GAME" button is also pure green at (0,128,0). To exclude it
    # we restrict the scan to the upper half of the screen (the 2x2 grid sits
    # in the upper-middle area). The START button is below the grid.
    scan_top, scan_bottom = 0, int(H * 0.65)

    def find_blob_center(rgb):
        r, g, b = rgb
        mask = (
            (arr[scan_top:scan_bottom, :, 0] == r) &
            (arr[scan_top:scan_bottom, :, 1] == g) &
            (arr[scan_top:scan_bottom, :, 2] == b)
        )
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return None, 0
        # Use the median to be robust to anti-aliased fringes
        return (int(np.median(xs)), int(np.median(ys) + scan_top)), int(mask.sum())

    centers = {}
    for color, rgb in targets.items():
        center, pixel_count = find_blob_center(rgb)
        if center is None:
            record("C", f"locate {color}", False,
                   f"no pixels matching RGB{rgb} in upper region")
            continue
        record("C", f"locate {color}", True,
               f"center={center}, {pixel_count} matching pixels")
        centers[color] = center

    if len(centers) < 4:
        print("\n  Couldn't locate all 4 buttons. Check that Game.py is in the")
        print("  foreground and the window isn't obscured.")
        return

    # Write calibration file the skill registry reads at import time
    calib_path = ROOT / "conf" / "simon_says_buttons.json"
    with open(calib_path, "w", encoding="utf-8") as f:
        json.dump({k: list(v) for k, v in centers.items()}, f, indent=2)
    record("C", "write calibration", True, f"saved {calib_path}")

    # Click each one and let the user see whether the button registers visually
    print("\n  Clicking each found center (2s gap) with pyautogui...")
    for color, (x, y) in centers.items():
        try:
            pyautogui.moveTo(x, y, duration=0.15)
            pyautogui.click(x, y)
            record("C", f"click {color} @ ({x},{y})", True)
        except Exception as e:
            traceback.print_exc()
            record("C", f"click {color} @ ({x},{y})", False,
                   f"{type(e).__name__}: {e}")
        time.sleep(2)


def main():
    global BasicClick, Wait, TakeScreenshot  # populated after calibration

    banner("Simon Says skills smoke test")
    print(
        "Pre-flight:\n"
        "  - Game.py must be running and the window visible.\n"
        "  - Don't touch the mouse while the test is running.\n"
        "  - On Windows, click away from this terminal so focus moves to the game.\n"
    )
    for n in (3, 2, 1):
        print(f"  starting in {n}...")
        time.sleep(1)

    # Phase 1: calibrate buttons by color — must precede skill registry import.
    section_c_real_window()

    # Phase 2: bootstrap cradle config now that calibration is on disk.
    from cradle.config import Config
    import cradle.gameio  # noqa: F401  (binds cradle.gameio attribute)
    import cradle.gameio.gui_utils  # noqa: F401
    config_inst = Config()
    config_inst.load_env_config(str(ROOT / "conf" / "env_config_simon_says.json"))

    # Phase 3: import the skill registry (this triggers _load_calibration()).
    from cradle.environment.simon_says.skill_registry import (
        SimonSaysSkillRegistry, BasicClick as _BC, Wait as _W, TakeScreenshot as _TS,
    )
    BasicClick, Wait, TakeScreenshot = _BC, _W, _TS

    skill_registry = SimonSaysSkillRegistry(skill_configs=config_inst.skill_configs)

    section_a_production_path(skill_registry)
    section_b_skills_direct()

    # Summary
    banner("Summary")
    width = max(len(name) for _, name, _, _ in results) + 2
    by_section = {"A": [], "B": [], "C": []}
    for sec, name, status, detail in results:
        by_section[sec].append((name, status, detail))
    for sec, items in by_section.items():
        print(f"\nSection {sec}:")
        for name, status, detail in items:
            print(f"  [{status}] {name:<{width}}{detail}")

    fails = [r for r in results if r[2] == FAIL]
    print()
    if not fails:
        print("All checks passed. The agent should be able to act on the game.")
        sys.exit(0)
    print(f"{len(fails)} check(s) failed.")
    print(
        "\nLikely root causes (read your traceback above):\n"
        "  - Section A FAIL but B PASS  => skill_registry.execute_skill doesn't\n"
        "       pass `gm`. Fix: bind gm to each skill in SimonSaysSkillRegistry,\n"
        "       e.g. use functools.partial(skill.execute, gm=self.gm).\n"
        "  - Section B FAIL on click     => BasicClick.execute calls\n"
        "       gm.io_env.mouse_click(x, y) which doesn't exist. Replace with:\n"
        "           gm.io_env.mouse_move(x, y); gm.io_env.mouse_click_button('left')\n"
        "  - Section B FAIL on screenshot => TakeScreenshot.execute calls\n"
        "       gm.take_screenshot(), which doesn't exist. Use gm.capture_screen().\n"
        "  - Section C PASS but A FAIL   => the wiring is broken but the real\n"
        "       coords work. Use the computed centers from Section C to update the\n"
        "       hardcoded button_positions dict.\n"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
