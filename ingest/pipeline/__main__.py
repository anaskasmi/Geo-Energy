"""One-shot entrypoint: `python -m pipeline` (container CMD; `docker compose run --rm ingest`)."""

from __future__ import annotations

import sys

from . import config, harness, logging_setup


def main() -> int:
    cfg = config.from_env()
    logging_setup.configure(cfg.log_level)
    harness.run(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
