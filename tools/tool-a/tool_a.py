"""tool_a.py – Main script for tool-a."""

import argparse
import sys
import os

# Allow importing from lib/python when run directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'lib', 'python'))

from common_utils import load_config, log  # noqa: E402


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog='tool-a',
        description='Example Python tool in the core-tools scaffold.',
    )
    parser.add_argument('input', help='Input value to process')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument(
        '--config',
        default=os.path.join(os.path.dirname(__file__), '..', '..', 'configs', 'defaults.yaml'),
        help='Path to YAML config file',
    )
    return parser.parse_args(argv)


def run(args):
    config = load_config(args.config)
    if args.verbose:
        log(f"Config loaded: {config}")
    result = args.input.upper()
    log(f"Result: {result}")
    return result


def main(argv=None):
    args = parse_args(argv)
    run(args)


if __name__ == '__main__':
    main()
