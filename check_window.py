"""
Diagnostic: find the ScummVM game window and show what coordinates the agent uses.

Run this while ScummVM is open:
    python check_window.py

It will:
  1. Print detected window position and size
  2. Take a full-desktop screenshot
  3. Draw a red border around the detected game region
  4. Save the result to /tmp/window_check.png  — open it to verify
"""

import os
import subprocess
import sys
import tempfile

os.environ.setdefault("DISPLAY", ":0")

ENV_NAME = "Kings Quest IV - The Perils of Love"


def find_window_wmctrl(name_fragment):
    try:
        out = subprocess.check_output(["wmctrl", "-lG"], text=True, env=os.environ)
    except FileNotFoundError:
        return None
    for line in out.splitlines():
        if name_fragment.lower() in line.lower():
            parts = line.split()
            # wmctrl -lG columns: id desktop x y w h host title...
            if len(parts) >= 6:
                x, y, w, h = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
                return x, y, w, h
    return None


def find_window_xdotool(name_fragment):
    try:
        ids = subprocess.check_output(
            ["xdotool", "search", "--name", name_fragment],
            text=True, env=os.environ, stderr=subprocess.DEVNULL,
        ).strip().splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    for wid in ids:
        try:
            out = subprocess.check_output(
                ["xdotool", "getwindowgeometry", wid],
                text=True, env=os.environ,
            )
            pos, geo = None, None
            for ln in out.splitlines():
                if "Position:" in ln:
                    coords = ln.split(":")[1].split("(")[0].strip()
                    pos = [int(v) for v in coords.split(",")]
                elif "Geometry:" in ln:
                    dims = ln.split(":")[1].strip()
                    geo = [int(v) for v in dims.split("x")]
            if pos and geo:
                return pos[0], pos[1], geo[0], geo[1]
        except Exception:
            pass
    return None


def take_screenshot():
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        tmp = f.name
    r = subprocess.run(
        ["gdbus", "call", "--session",
         "--dest", "org.gnome.Shell",
         "--object-path", "/org/gnome/Shell/Screenshot",
         "--method", "org.gnome.Shell.Screenshot.Screenshot",
         "false", "false", tmp],
        env=os.environ, capture_output=True, text=True, timeout=10,
    )
    if r.returncode == 0 and os.path.exists(tmp):
        return tmp
    # fallback: gnome-screenshot
    subprocess.run(["pkill", "-9", "-f", "gnome-screenshot"], capture_output=True)
    import time; time.sleep(0.5)
    r2 = subprocess.run(
        ["gnome-screenshot", "-f", tmp, "--delay=0"],
        env=os.environ, capture_output=True, timeout=20,
    )
    if r2.returncode == 0 and os.path.exists(tmp):
        return tmp
    return None


def main():
    print(f"\nLooking for window: '{ENV_NAME}'")

    geom = find_window_wmctrl(ENV_NAME) or find_window_xdotool(ENV_NAME)

    # Also list all visible windows so the user can see what's there
    print("\nAll visible windows (wmctrl -lG):")
    try:
        out = subprocess.check_output(["wmctrl", "-lG"], text=True, env=os.environ)
        for line in out.splitlines():
            print(" ", line)
    except Exception as e:
        print(f"  (wmctrl failed: {e})")

    if geom is None:
        # Try partial matches
        print(f"\nNo exact match. Trying partial names...")
        try:
            out = subprocess.check_output(["wmctrl", "-lG"], text=True, env=os.environ)
            for frag in ["scummvm", "quest", "kq4", "peril"]:
                for line in out.splitlines():
                    if frag.lower() in line.lower():
                        parts = line.split()
                        if len(parts) >= 6:
                            x, y, w, h = int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5])
                            geom = (x, y, w, h)
                            print(f"  Partial match on '{frag}': {line.strip()}")
                            break
                if geom:
                    break
        except Exception:
            pass

    if geom:
        x, y, w, h = geom
        print(f"\n✓ Game window found:")
        print(f"  Position: left={x}, top={y}")
        print(f"  Size:     width={w}, height={h}")
        print(f"  Bottom-right corner: ({x+w}, {y+h})")
        print(f"\n  env_region should be: [{x}, {y}, {w}, {h}]")
        print(f"  Agent coordinate space: (0,0) = top-left of game, ({w},{h}) = bottom-right")
        print(f"  IMPORTANT: agent coords are relative to game window, not desktop!")
    else:
        print("\n✗ Game window NOT found. Is ScummVM running?")
        print("  Check the window list above for the correct name.")

    print("\nTaking screenshot...")
    tmp_shot = take_screenshot()
    if tmp_shot:
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.open(tmp_shot).convert("RGB")
            draw = ImageDraw.Draw(img)

            if geom:
                x, y, w, h = geom
                # Draw red border around detected game region
                for t in range(4):
                    draw.rectangle([x+t, y+t, x+w-t, y+h-t], outline="red")
                # Label corners
                draw.text((x+10, y+10), f"GAME WINDOW\n({x},{y}) to ({x+w},{y+h})\n{w}x{h}px", fill="red")
                # Mark center
                cx, cy = x + w//2, y + h//2
                draw.ellipse([cx-8, cy-8, cx+8, cy+8], fill="red")
                draw.text((cx+12, cy), f"center ({w//2},{h//2})", fill="red")

            out_path = "/tmp/window_check.png"
            img.save(out_path)
            print(f"✓ Annotated screenshot saved to: {out_path}")
            print(f"  Open it to verify the red box is around the game area.")
        except ImportError:
            import shutil
            shutil.copy(tmp_shot, "/tmp/window_check.png")
            print("✓ Screenshot saved to /tmp/window_check.png (PIL not available for annotation)")
        finally:
            os.unlink(tmp_shot)
    else:
        print("✗ Screenshot failed")


if __name__ == "__main__":
    main()
