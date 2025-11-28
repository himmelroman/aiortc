"""
Event loop profiler to identify what's blocking packet reception.

This module instruments asyncio to track:
1. Which tasks/coroutines are running
2. How long each task runs before yielding
3. What's preventing _recv() from being scheduled quickly
"""
import asyncio
import time
import logging
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)


class EventLoopProfiler:
    """Profile asyncio event loop to find blocking tasks"""

    def __init__(self, report_interval: float = 2.0):
        self.report_interval = report_interval
        self.task_run_times = defaultdict(lambda: {'count': 0, 'total_us': 0, 'max_us': 0})
        self.last_report = time.time()
        self._original_task_step = None
        self._active = False

    def start(self):
        """Start profiling the event loop"""
        if self._active:
            return

        # Patch asyncio.Task._step to measure execution time
        import asyncio.tasks
        self._original_task_step = asyncio.tasks.Task._step

        def profiled_step(task_self, exc=None):
            start_us = int(time.monotonic() * 1_000_000)

            # Call original _step
            result = self._original_task_step(task_self, exc)

            end_us = int(time.monotonic() * 1_000_000)
            duration_us = end_us - start_us

            # Track task execution time
            if duration_us > 100:  # Only track if >100μs (significant)
                task_name = self._get_task_name(task_self)
                stats = self.task_run_times[task_name]
                stats['count'] += 1
                stats['total_us'] += duration_us
                stats['max_us'] = max(stats['max_us'], duration_us)

            # Report periodically
            now = time.time()
            if now - self.last_report >= self.report_interval:
                self._report()
                self.last_report = now

            return result

        asyncio.tasks.Task._step = profiled_step
        self._active = True
        logger.info("🔍 Event loop profiler STARTED")

    def stop(self):
        """Stop profiling"""
        if not self._active:
            return

        if self._original_task_step:
            import asyncio.tasks
            asyncio.tasks.Task._step = self._original_task_step

        self._active = False
        self._report()  # Final report
        logger.info("🔍 Event loop profiler STOPPED")

    def _get_task_name(self, task) -> str:
        """Get a readable name for a task"""
        try:
            coro = task.get_coro()
            coro_name = coro.__name__ if hasattr(coro, '__name__') else str(coro)

            # Simplify common patterns
            if 'RTCRtpSender' in coro_name or '_next_encoded_frame' in coro_name:
                return 'RTCRtpSender._next_encoded_frame'
            elif 'RTCRtpReceiver' in coro_name or '_handle_rtp_packet' in coro_name:
                return 'RTCRtpReceiver._handle_rtp_packet'
            elif '_recv_next' in coro_name or 'RTCDtlsTransport' in coro_name:
                return 'RTCDtlsTransport._recv_next'
            elif 'ColorBarVideoTrack' in coro_name or 'recv' in coro_name:
                return 'VideoTrack.recv'
            elif 'monitor' in coro_name.lower():
                return 'monitor_task'
            else:
                return coro_name
        except:
            return 'unknown'

    def _report(self):
        """Print report of task execution times"""
        if not self.task_run_times:
            return

        logger.info("🔍 EVENT_LOOP_PROFILE (tasks blocking event loop):")

        # Sort by total time (most blocking first)
        sorted_tasks = sorted(
            self.task_run_times.items(),
            key=lambda x: x[1]['total_us'],
            reverse=True
        )

        for task_name, stats in sorted_tasks[:10]:  # Top 10
            avg_us = stats['total_us'] / stats['count'] if stats['count'] > 0 else 0
            logger.info(f"  {task_name}: "
                       f"count={stats['count']}, "
                       f"avg={avg_us:.0f}μs ({avg_us/1000:.2f}ms), "
                       f"max={stats['max_us']}μs ({stats['max_us']/1000:.2f}ms), "
                       f"total={stats['total_us']/1000:.1f}ms")

        # Reset stats
        self.task_run_times.clear()


# Global profiler instance
_profiler: Optional[EventLoopProfiler] = None


def enable_event_loop_profiling(report_interval: float = 2.0):
    """Enable event loop profiling globally"""
    global _profiler
    if _profiler is None:
        _profiler = EventLoopProfiler(report_interval)
    _profiler.start()


def disable_event_loop_profiling():
    """Disable event loop profiling"""
    global _profiler
    if _profiler:
        _profiler.stop()
