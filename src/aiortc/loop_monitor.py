"""
Simple event loop monitor using asyncio debugging.

Instead of patching Task._step (which is C code), we'll:
1. Enable asyncio debug mode to detect slow callbacks
2. Add manual instrumentation to key coroutines
3. Monitor task count and pending tasks
"""
import asyncio
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class LoopMonitor:
    """Monitor event loop health and task execution"""

    def __init__(self, check_interval: float = 2.0):
        self.check_interval = check_interval
        self.task = None
        self._running = False

    async def _monitor_loop(self):
        """Periodically check event loop state"""
        while self._running:
            try:
                # Get all tasks
                all_tasks = asyncio.all_tasks()

                # Categorize tasks by coroutine name
                task_categories = defaultdict(int)
                for task in all_tasks:
                    try:
                        coro = task.get_coro()
                        coro_name = coro.__qualname__ if hasattr(coro, '__qualname__') else str(coro)

                        # Simplify names
                        if 'RTCRtpSender' in coro_name or '_next_encoded_frame' in coro_name:
                            category = 'RTCRtpSender(encoding)'
                        elif 'RTCRtpReceiver' in coro_name or '_handle_rtp_packet' in coro_name:
                            category = 'RTCRtpReceiver(decoding)'
                        elif '_recv_next' in coro_name or 'RTCDtlsTransport' in coro_name:
                            category = 'RTCDtlsTransport(network)'
                        elif 'ColorBarVideoTrack' in coro_name or 'recv' in coro_name.lower():
                            category = 'VideoTrack(frame_gen)'
                        elif 'monitor' in coro_name.lower():
                            category = 'monitoring'
                        elif 'aiohttp' in coro_name.lower() or 'web' in coro_name.lower():
                            category = 'aiohttp(web_server)'
                        else:
                            category = f'other({coro_name[:30]})'

                        task_categories[category] += 1
                    except:
                        task_categories['unknown'] += 1

                # Log task breakdown
                logger.info(f"🔍 LOOP_MONITOR: {len(all_tasks)} total tasks")
                for category, count in sorted(task_categories.items(), key=lambda x: -x[1]):
                    logger.info(f"   {category}: {count}")

            except Exception as e:
                logger.error(f"Loop monitor error: {e}")

            await asyncio.sleep(self.check_interval)

    def start(self):
        """Start monitoring"""
        if self._running:
            return

        self._running = True
        # Enable asyncio slow callback warnings (>100ms)
        loop = asyncio.get_event_loop()
        loop.slow_callback_duration = 0.1  # 100ms
        loop.set_debug(True)

        self.task = asyncio.create_task(self._monitor_loop())
        logger.info("🔍 Loop monitor STARTED (asyncio debug mode enabled)")

    def stop(self):
        """Stop monitoring"""
        if not self._running:
            return

        self._running = False
        if self.task:
            self.task.cancel()

        loop = asyncio.get_event_loop()
        loop.set_debug(False)
        logger.info("🔍 Loop monitor STOPPED")


_monitor = None


def start_loop_monitor(check_interval: float = 2.0):
    """Start the global loop monitor"""
    global _monitor
    if _monitor is None:
        _monitor = LoopMonitor(check_interval)
    _monitor.start()


def stop_loop_monitor():
    """Stop the global loop monitor"""
    global _monitor
    if _monitor:
        _monitor.stop()
