"""Entry point for `python -m podcaster_ai`."""

import sys

from .run import cli

if __name__ == "__main__":
    sys.exit(cli())
