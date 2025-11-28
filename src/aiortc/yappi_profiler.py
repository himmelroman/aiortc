"""
Yappi-based profiler for asyncio applications.

Yappi (Yet Another Python Profiler) supports:
- Asyncio coroutines
- Thread profiling
- Wall-time and CPU-time measurements
"""
import logging
import yappi

logger = logging.getLogger(__name__)


class YappiProfiler:
    """Yappi profiler wrapper"""

    def __init__(self):
        self.running = False

    def start(self):
        """Start profiling with yappi"""
        if self.running:
            return

        yappi.set_clock_type("wall")  # Wall time (includes I/O wait)
        yappi.start()
        self.running = True
        logger.info("🔍 Yappi profiler STARTED (wall-time mode)")

    def stop_and_print(self, top_n: int = 30):
        """Stop profiling and print results"""
        if not self.running:
            return

        yappi.stop()
        self.running = False

        logger.info("🔍 Yappi profiler STOPPED - Top functions by total time:")
        logger.info("=" * 100)

        # Get function stats sorted by total time (descending)
        stats = yappi.get_func_stats()
        stats.sort("tsub")  # tsub = total time including subcalls
        stats = list(reversed(stats))  # Reverse to get descending order

        # Print top N functions
        for i, stat in enumerate(stats[:top_n], 1):
            logger.info(f"  #{i}: {stat.name}")
            logger.info(f"      Total: {stat.tsub:.3f}s | Own: {stat.ttot:.3f}s | "
                       f"Calls: {stat.ncall} | Avg: {stat.tavg*1000:.2f}ms")

        logger.info("=" * 100)

        # Clear stats
        yappi.clear_stats()


_profiler = None


def start_yappi_profiling():
    """Start yappi profiling"""
    global _profiler
    if _profiler is None:
        _profiler = YappiProfiler()
    _profiler.start()


def stop_yappi_profiling(top_n: int = 30):
    """Stop yappi profiling and print results"""
    global _profiler
    if _profiler:
        _profiler.stop_and_print(top_n)
