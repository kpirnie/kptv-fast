#!/usr/bin/env python3
"""
KPTV FAST Streams — entry point.

gevent monkey-patching MUST happen before any other import.
"""

# CRITICAL: must be first
from gevent import monkey  # type: ignore
monkey.patch_all()

import sys

sys.setrecursionlimit(2000)

from utils.logging_config import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)

from core.aggregator import UnifiedStreamingAggregator


if __name__ == '__main__':
    try:
        agg = UnifiedStreamingAggregator()
        agg.run()
    except Exception as exc:
        logger.error(f"Failed to start: {exc}")
        sys.exit(1)