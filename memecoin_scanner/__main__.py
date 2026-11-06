"""Lets `python -m memecoin_scanner` run the scan (same flags as `run-scan`)."""

from .scan import main

if __name__ == "__main__":
    raise SystemExit(main())
