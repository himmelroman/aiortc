"""
cProfile helper for profiling asyncio applications
"""
import cProfile
import pstats
import io
import logging

logger = logging.getLogger(__name__)

_profiler = None


def start_cprofile():
    """Start cProfile profiling"""
    global _profiler
    _profiler = cProfile.Profile()
    _profiler.enable()
    logger.info("🔍 cProfile profiling STARTED")


def stop_and_dump_cprofile(top_n: int = 50):
    """Stop profiling and dump results"""
    global _profiler
    if not _profiler:
        return

    _profiler.disable()

    # Create string buffer to capture stats
    s = io.StringIO()
    ps = pstats.Stats(_profiler, stream=s)
    ps.strip_dirs()
    ps.sort_stats('cumulative')  # Sort by cumulative time

    logger.info("🔍 cProfile RESULTS - Top functions by cumulative time:")
    logger.info("=" * 120)

    # Print top N functions
    ps.print_stats(top_n)

    # Log the results
    for line in s.getvalue().split('\n'):
        if line.strip():
            logger.info(line)

    logger.info("=" * 120)

    _profiler = None
