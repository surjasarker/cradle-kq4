import os
import signal
import subprocess
import tempfile
import time

from PIL import Image

from cradle.config import Config
from cradle.log import Logger
from cradle.gameio.io_env import IOEnvironment
from cradle.utils.template_matching import match_template_image
from cradle import constants
from cradle.environment import UIControl

config = Config()
logger = Logger()
io_env = IOEnvironment()


class ScummVMUIControl(UIControl):
    """UI Control for ScummVM games.
    
    Implements pause/resume using quicksave/quickload (F7/F8) which are
    standard in most ScummVM games. This allows the VLM to pause the game
    while it thinks, then resume from exactly the same position.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._pause_timeout = 5.0  # Max time to wait for pause to complete
        self._scummvm_process = None  # Track the ScummVM process

    def pause_game(self, env_name: str, ide_name: str) -> None:
        """Pause the game by saving to quicksave slot and returning to menu.
        
        Args:
            env_name: ScummVM game window name
            ide_name: IDE window name (optional, for showing IDE during pause)
        """
        if self.is_env_paused():
            logger.debug("Environment is already paused")
            return
        
        # Switch to IDE window to show it while paused (for debugging)
        if ide_name:
            try:
                ide_window = io_env.get_windows_by_name(ide_name)[0]
                ide_window.activate()
                ide_window.show()
                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"Could not switch to IDE: {e}")
        
        # Quicksave the game (F7 in most ScummVM games)
        logger.debug("Taking quicksave (F7)...")
        io_env.key_press('f7')
        time.sleep(constants.PAUSE_SCREEN_WAIT)

    def unpause_game(self, env_name: str, ide_name: str) -> None:
        """Resume the game from quicksave.
        
        Args:
            env_name: ScummVM game window name
            ide_name: IDE window name (not used for unpause)
        """
        if not self.is_env_paused():
            logger.debug("Environment is not paused")
            return
        
        # Switch back to game window
        try:
            target_window = io_env.get_windows_by_name(env_name)[0]
            target_window.activate()
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"Could not activate game window: {e}")
            return
        
        # Quickload the game (F8 in most ScummVM games)
        logger.debug("Loading quicksave (F8)...")
        io_env.key_press('f8')
        time.sleep(constants.PAUSE_SCREEN_WAIT)

    def switch_to_game(self, env_name: str, ide_name: str) -> None:
        """Switch focus to the ScummVM game window and ensure it's running.
        
        Args:
            env_name: ScummVM game window name
            ide_name: IDE window name (not used)
        """
        try:
            named_windows = io_env.get_windows_by_name(env_name)
            if len(named_windows) == 0:
                # Game window doesn't exist, launch ScummVM
                logger.info(f"Game window '{env_name}' not found, launching ScummVM...")
                self._launch_scummvm_game()
                # Wait for game to start
                time.sleep(3)
                # Try to find the window again
                named_windows = io_env.get_windows_by_name(env_name)
                if len(named_windows) == 0:
                    logger.error(f"Could not find or launch game window {env_name}")
                    return
            
            target_window = named_windows[0]
            target_window.activate()
            time.sleep(0.5)
        except Exception as e:
            if "Error code from Windows: 0" in str(e):
                # Handle pygetwindow exception
                logger.warn("Could not activate game window (pygetwindow error)")
            else:
                logger.error(f"Error switching to game: {e}")
                raise e
        
        # Ensure game is not paused
        if self.is_env_paused():
            self.unpause_game(env_name, ide_name)

    def exit_back_to_pause(self, env_name: str, ide_name: str) -> None:
        """Exit any menu/dialog back to pause state by saving and exiting.
        
        Args:
            env_name: ScummVM game window name
            ide_name: IDE window name
        """
        max_steps = 10
        back_steps = 0
        
        while not self.is_env_paused() and back_steps < max_steps:
            back_steps += 1
            # Press Escape to close any dialogs/menus
            io_env.key_press('esc')
            time.sleep(constants.PAUSE_SCREEN_WAIT)
        
        if back_steps >= max_steps:
            logger.warn("Environment did not reach pause state after max steps")
        
        # Ensure we have a quicksave
        self.pause_game(env_name, ide_name)

    def exit_back_to_game(self, env_name: str, ide_name: str) -> None:
        """Exit back to active game from any pause/menu state.
        
        Args:
            env_name: ScummVM game window name
            ide_name: IDE window name
        """
        # First ensure we're in pause state
        self.exit_back_to_pause(env_name, ide_name)
        
        # Then resume from pause
        self.unpause_game(env_name, ide_name)

    def is_env_paused(self) -> bool:
        """Check if the ScummVM environment is paused.
        
        An environment is considered paused if the game window is not active
        or if the GUI is visible (menu/dialog).
        
        Returns:
            True if environment is paused, False otherwise
        """
        try:
            target_window = io_env.get_windows_by_name(config.env_name)[0]
            is_active = target_window.is_active()
            return not is_active
        except Exception as e:
            logger.debug(f"Error checking pause state: {e}")
            return False

    def take_screenshot(self,
                        tid: float,
                        screen_region: tuple[int, int, int, int] = None) -> str:
        """Take a screenshot on GNOME Wayland.

        Tries two methods in order:
        1. org.gnome.Shell.Screenshot via gdbus — synchronous, no GApplication
           registration, works from any subprocess in the same D-Bus session.
        2. gnome-screenshot -f — fallback; kills any stale instance first to
           avoid the 'already registered' D-Bus hang.
        """
        if screen_region is None:
            screen_region = config.env_region

        left, top, width, height = screen_region
        output_dir = config.work_dir
        screen_image_filename = output_dir + "/screen_" + str(tid) + ".jpg"

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            captured = False

            # --- Method 1: org.gnome.Shell.Screenshot (synchronous D-Bus call) ---
            r = subprocess.run(
                ["gdbus", "call", "--session",
                 "--dest", "org.gnome.Shell",
                 "--object-path", "/org/gnome/Shell/Screenshot",
                 "--method", "org.gnome.Shell.Screenshot.Screenshot",
                 "false", "false", tmp_path],
                env=os.environ, capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0 and os.path.exists(tmp_path):
                captured = True
                logger.debug("Screenshot via org.gnome.Shell.Screenshot")
            else:
                logger.debug(f"org.gnome.Shell.Screenshot failed ({r.stderr.strip()}), trying gnome-screenshot")

            # --- Method 2: gnome-screenshot ---
            # Kill any stopped/zombie gnome-screenshot that may hold the D-Bus
            # name (caused by previous TimeoutExpired not cleaning up properly).
            if not captured:
                subprocess.run(["pkill", "-9", "-f", "gnome-screenshot"],
                               capture_output=True)
                time.sleep(0.5)
                proc = subprocess.Popen(
                    ["gnome-screenshot", "-f", tmp_path, "--delay=0"],
                    env=os.environ,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,   # own process group → clean kill
                )
                try:
                    rc2 = proc.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    rc2 = -1
                if rc2 == 0 and os.path.exists(tmp_path):
                    captured = True
                    logger.debug("Screenshot via gnome-screenshot")

            # --- Method 3: systemd-run --user gives a proper D-Bus service context ---
            if not captured:
                r3 = subprocess.run(
                    ["systemd-run", "--user", "--collect",
                     f"--setenv=DISPLAY={os.environ.get('DISPLAY', ':0')}",
                     f"--setenv=WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', 'wayland-0')}",
                     f"--setenv=DBUS_SESSION_BUS_ADDRESS={os.environ.get('DBUS_SESSION_BUS_ADDRESS', '')}",
                     f"--setenv=XDG_RUNTIME_DIR={os.environ.get('XDG_RUNTIME_DIR', '')}",
                     "--wait",
                     "gnome-screenshot", "-f", tmp_path, "--delay=0"],
                    env=os.environ, capture_output=True, text=True, timeout=30,
                )
                if r3.returncode == 0 and os.path.exists(tmp_path):
                    captured = True
                    logger.debug("Screenshot via systemd-run gnome-screenshot")
                else:
                    raise RuntimeError(
                        f"All screenshot methods failed.\n"
                        f"  gdbus: {r.stderr.strip()}\n"
                        f"  gnome-screenshot: rc={r2.returncode}\n"
                        f"  systemd-run: {r3.stderr.strip()}"
                    )

            img = Image.open(tmp_path).convert("RGB")
            img_cropped = img.crop((left, top, left + width, top + height))
            img_cropped.save(screen_image_filename)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

        return screen_image_filename

    def _launch_scummvm_game(self):
        """Launch ScummVM with the configured game.
        
        Uses the paths from config to launch ScummVM with Kings Quest 4.
        """
        try:
            scummvm_exe = getattr(config, 'scummvm_executable', '/home/surja/Downloads/fomo_project/scummvm/bin/scummvm-linux/scummvm')
            game_path = getattr(config, 'scummvm_game_path', '/home/surja/Downloads/fomo_project/scummvm/games/KQ4')
            game_id = getattr(config, 'scummvm_game_id', 'kq4')
            
            logger.info(f"Launching ScummVM: {scummvm_exe}")
            logger.info(f"Game path: {game_path}")
            logger.info(f"Game ID: {game_id}")
            
            # Launch ScummVM in background
            self._scummvm_process = subprocess.Popen([
                scummvm_exe,
                '--path=' + game_path,
                game_id
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            logger.info("ScummVM launched successfully")
            
        except Exception as e:
            logger.error(f"Failed to launch ScummVM: {e}")
            raise
