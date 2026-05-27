#!/usr/bin/env python
"""Launcher script for Simon Says AI game."""

import sys
import os
import argparse

# Add project root to path
sys.path.insert(0, os.path.dirname(__file__))

from runner import get_args_parser, main
from cradle.config import Config

config = Config()

if __name__ == '__main__':
    parser = get_args_parser()
    
    # Update default for Simon Says
    parser.set_defaults(
        llmProviderConfig="./conf/openai_config.json",
        embedProviderConfig="./conf/openai_config.json",
        envConfig="./conf/env_config_simon_says.json"
    )
    
    args = parser.parse_args()
    
    # Load config
    config.load_env_config(args.envConfig)
    config.set_fixed_seed()
    
    # Run
    main(args)
