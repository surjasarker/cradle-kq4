import argparse
import glob
import importlib
import os
import subprocess

# Ensure XWayland auth cookie is available before pyautogui/Xlib imports.
# On GNOME Wayland the cookie lives in a mutter temp file stored under a
# non-standard key; extract the raw cookie and register it explicitly for
# display :0 so Python-Xlib's strict key matching finds it.
_mutter_auth_files = glob.glob("/run/user/1000/.mutter-Xwaylandauth.*")
if _mutter_auth_files:
    os.environ["XAUTHORITY"] = _mutter_auth_files[0]
    _list = subprocess.run(
        ["xauth", "-f", _mutter_auth_files[0], "list"],
        capture_output=True, text=True,
    )
    for _line in _list.stdout.strip().splitlines():
        _parts = _line.split()
        if len(_parts) == 3:
            subprocess.run(
                ["xauth", "add", ":0", ".", _parts[2]],
                capture_output=True,
            )
            break

from cradle.config import Config
from cradle.gameio import GameManager
from cradle.log import Logger

config = Config()
logger = Logger()


def main(args):

    # Choose shared or specific runner
    if config.env_shared_runner is not None and len(config.env_shared_runner) > 0:
        runner_key = config.env_shared_runner.lower()
    else:
        runner_key = config.env_short_name.lower()

    # Load the runner module
    runner_module = importlib.import_module(f'cradle.runner.{runner_key}_runner')
    entry = getattr(runner_module, 'entry')

    # Run the entry
    entry(args)


def get_args_parser():

    parser = argparse.ArgumentParser("Cradle Agent Runner")
    parser.add_argument("--llmProviderConfig", type=str, default="./conf/openai_config.json", help="The path to the LLM provider config file")
    parser.add_argument("--embedProviderConfig", type=str, default="./conf/openai_config.json", help="The path to the embedding model provider config file")
    parser.add_argument("--envConfig", type=str, default="./conf/env_config_outlook.json", help="The path to the environment config file")
    return parser


if __name__ == '__main__':
    parser = get_args_parser()
    args = parser.parse_args()

    config.load_env_config(args.envConfig)
    config.set_fixed_seed()

    main(args)
