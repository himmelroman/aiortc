"""
Lightweight async-aware profiler for tracking time spent in different code sections.
"""

import time
import logging
from contextlib import contextmanager
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class SectionProfiler:
    """
    Tracks time spent in different named sections of code.
    Thread-safe and designed for async code.
    """

    def __init__(self, name: str = "default", report_interval: float = 5.0):
        self.name = name
        self.report_interval = report_interval
        self.sections: Dict[str, Dict[str, float]] = {}
        self.last_report_time = time.time()
        self.enabled = True

    @contextmanager
    def section(self, section_name: str):
        """Context manager to time a section of code."""
        if not self.enabled:
            yield
            return

        start_time = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - start_time
            elapsed_us = elapsed * 1_000_000

            if section_name not in self.sections:
                self.sections[section_name] = {
                    'count': 0,
                    'total_us': 0,
                    'min_us': float('inf'),
                    'max_us': 0,
                }

            stats = self.sections[section_name]
            stats['count'] += 1
            stats['total_us'] += elapsed_us
            stats['min_us'] = min(stats['min_us'], elapsed_us)
            stats['max_us'] = max(stats['max_us'], elapsed_us)

            # Auto-report periodically
            now = time.time()
            if now - self.last_report_time >= self.report_interval:
                self.report()
                self.last_report_time = now

    def report(self):
        """Report timing statistics for all sections."""
        if not self.sections:
            return

        logger.info(f"🔍 PROFILER[{self.name}] Report:")

        # Sort by total time spent (descending)
        sorted_sections = sorted(
            self.sections.items(),
            key=lambda x: x[1]['total_us'],
            reverse=True
        )

        for section_name, stats in sorted_sections:
            if stats['count'] == 0:
                continue

            avg_us = stats['total_us'] / stats['count']
            logger.info(f"  {section_name}: "
                       f"count={stats['count']}, "
                       f"total={stats['total_us']/1000:.1f}ms, "
                       f"avg={avg_us:.0f}μs, "
                       f"min={stats['min_us']:.0f}μs, "
                       f"max={stats['max_us']:.0f}μs")

    def reset(self):
        """Reset all timing statistics."""
        self.sections.clear()
        self.last_report_time = time.time()


# Global profiler instance
_rtp_profiler: Optional[SectionProfiler] = None


def get_rtp_profiler() -> SectionProfiler:
    """Get or create the global RTP profiler."""
    global _rtp_profiler
    if _rtp_profiler is None:
        _rtp_profiler = SectionProfiler(name="RTP_PATH", report_interval=5.0)
    return _rtp_profiler
