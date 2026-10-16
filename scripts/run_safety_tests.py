#!/usr/bin/env python3
"""Run the fat-pointer safety test suite."""

from __future__ import annotations

import sys

from run_tests import main


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], suite="safety"))
