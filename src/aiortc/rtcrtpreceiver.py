import asyncio
import datetime
import logging
import queue
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from av.frame import Frame

from . import clock
from .codecs import depayload, get_capabilities, get_decoder, is_rtx
from .exceptions import InvalidStateError
from .jitterbuffer import JitterBuffer
from .mediastreams import MediaStreamError, MediaStreamTrack
from .rate import RemoteBitrateEstimator
from .rtcdtlstransport import RTCDtlsTransport
from .rtcrtpparameters import (
    RTCRtpCapabilities,
    RTCRtpCodecParameters,
    RTCRtpReceiveParameters,
)
from .rtp import (
    RTCP_PSFB_APP,
    RTCP_PSFB_PLI,
    RTCP_RTPFB_NACK,
    RTP_HISTORY_SIZE,
    AnyRtcpPacket,
    RtcpByePacket,
    RtcpPsfbPacket,
    RtcpReceiverInfo,
    RtcpRrPacket,
    RtcpRtpfbPacket,
    RtcpSrPacket,
    RtpPacket,
    clamp_packets_lost,
    pack_remb_fci,
    unwrap_rtx,
)
from .stats import (
    RTCInboundRtpStreamStats,
    RTCRemoteOutboundRtpStreamStats,
    RTCStatsReport,
)
from .utils import uint16_add, uint16_gt

logger = logging.getLogger(__name__)


def decoder_worker(
    loop: asyncio.AbstractEventLoop, input_q: queue.Queue, output_q: asyncio.Queue
) -> None:
    codec_name = None
    decoder = None

    # RX bottleneck instrumentation
    frame_count = 0
    total_decode_time = 0.0
    last_report_time = time.time()

    while True:
        task = input_q.get()
        if task is None:
            # inform the track that is has ended
            asyncio.run_coroutine_threadsafe(output_q.put(None), loop)
            break
        codec, encoded_frame = task

        if codec.name != codec_name:
            decoder = get_decoder(codec)
            codec_name = codec.name

        # RX bottleneck instrumentation: Time the decode operation
        decode_start = time.time()
        frames_decoded = 0
        for frame in decoder.decode(encoded_frame):
            frames_decoded += 1
            # pass the decoded frame to the track
            asyncio.run_coroutine_threadsafe(output_q.put(frame), loop)
        decode_time = time.time() - decode_start

        # Track statistics
        frame_count += frames_decoded
        total_decode_time += decode_time

        # Report every second
        now = time.time()
        if now - last_report_time >= 1.0:
            elapsed = now - last_report_time
            avg_decode_ms = (total_decode_time / frame_count * 1000) if frame_count > 0 else 0
            qsize = input_q.qsize()

            logger.info(f"🔍 DECODER: {frame_count} frames/s, "
                       f"avg_decode={avg_decode_ms:.1f}ms, queue={qsize}")

            # Reset counters
            frame_count = 0
            total_decode_time = 0.0
            last_report_time = now

    if decoder is not None:
        del decoder


class NackGenerator:
    def __init__(self) -> None:
        self.max_seq: Optional[int] = None
        self.missing: set[int] = set()

    def add(self, packet: RtpPacket) -> bool:
        """
        Mark a new packet as received, and deduce missing packets.
        """
        missed = False

        if self.max_seq is None:
            self.max_seq = packet.sequence_number
            return missed

        # mark missing packets
        if uint16_gt(packet.sequence_number, self.max_seq):
            seq = uint16_add(self.max_seq, 1)
            while uint16_gt(packet.sequence_number, seq):
                self.missing.add(seq)
                missed = True
                seq = uint16_add(seq, 1)
            self.max_seq = packet.sequence_number
        else:
            self.missing.discard(packet.sequence_number)

        # limit number of tracked packets
        self.truncate()

        return missed

    def truncate(self) -> None:
        """
        Limit the number of missing packets we track.

        Otherwise, the size of RTCP FB messages grows indefinitely.
        """
        if self.max_seq is not None:
            min_seq = uint16_add(self.max_seq, -RTP_HISTORY_SIZE)
            for seq in list(self.missing):
                if uint16_gt(min_seq, seq):
                    self.missing.discard(seq)


class StreamStatistics:
    def __init__(self, clockrate: int) -> None:
        self.base_seq: Optional[int] = None
        self.max_seq: Optional[int] = None
        self.cycles = 0
        self.packets_received = 0

        # jitter
        self._clockrate = clockrate
        self._jitter_q4 = 0
        self._last_arrival: Optional[int] = None
        self._last_timestamp: Optional[int] = None

        # fraction lost
        self._expected_prior = 0
        self._received_prior = 0

    def add(self, packet: RtpPacket) -> None:
        in_order = self.max_seq is None or uint16_gt(
            packet.sequence_number, self.max_seq
        )
        self.packets_received += 1

        if self.base_seq is None:
            self.base_seq = packet.sequence_number

        if in_order:
            arrival = int(time.time() * self._clockrate)

            if self.max_seq is not None and packet.sequence_number < self.max_seq:
                self.cycles += 1 << 16
            self.max_seq = packet.sequence_number

            if packet.timestamp != self._last_timestamp and self.packets_received > 1:
                diff = abs(
                    (arrival - self._last_arrival)
                    - (packet.timestamp - self._last_timestamp)
                )
                self._jitter_q4 += diff - ((self._jitter_q4 + 8) >> 4)

            self._last_arrival = arrival
            self._last_timestamp = packet.timestamp

    @property
    def fraction_lost(self) -> int:
        expected_interval = self.packets_expected - self._expected_prior
        self._expected_prior = self.packets_expected
        received_interval = self.packets_received - self._received_prior
        self._received_prior = self.packets_received
        lost_interval = expected_interval - received_interval
        if expected_interval == 0 or lost_interval <= 0:
            return 0
        else:
            return (lost_interval << 8) // expected_interval

    @property
    def jitter(self) -> int:
        return self._jitter_q4 >> 4

    @property
    def packets_expected(self) -> int:
        return self.cycles + self.max_seq - self.base_seq + 1

    @property
    def packets_lost(self) -> int:
        return clamp_packets_lost(self.packets_expected - self.packets_received)


class RemoteStreamTrack(MediaStreamTrack):
    def __init__(self, kind: str, id: Optional[str] = None) -> None:
        super().__init__()
        self.kind = kind
        if id is not None:
            self._id = id
        self._queue: asyncio.Queue = asyncio.Queue()

    async def recv(self) -> Frame:
        """
        Receive the next frame.
        """
        if self.readyState != "live":
            raise MediaStreamError

        frame = await self._queue.get()
        if frame is None:
            self.stop()
            raise MediaStreamError
        return frame


class TimestampMapper:
    def __init__(self) -> None:
        self._last: Optional[int] = None
        self._origin: Optional[int] = None

    def map(self, timestamp: int) -> int:
        if self._origin is None:
            # first timestamp
            self._origin = timestamp
        elif timestamp < self._last:
            # RTP timestamp wrapped
            self._origin -= 1 << 32

        self._last = timestamp
        return timestamp - self._origin


@dataclass
class RTCRtpContributingSource:
    """
    The :class:`RTCRtpContributingSource` dictionary contains information about
    a contributing source (CSRC).
    """

    timestamp: datetime.datetime
    "The timestamp associated with this source."
    source: int
    "The CSRC identifier associated with this source."


@dataclass
class RTCRtpSynchronizationSource:
    """
    The :class:`RTCRtpSynchronizationSource` dictionary contains information about
    a synchronization source (SSRC).
    """

    timestamp: datetime.datetime
    "The timestamp associated with this source."
    source: int
    "The SSRC identifier associated with this source."


class RTCRtpReceiver:
    """
    The :class:`RTCRtpReceiver` interface manages the reception and decoding
    of data for a :class:`MediaStreamTrack`.

    :param kind: The kind of media (`'audio'` or `'video'`).
    :param transport: An :class:`RTCDtlsTransport`.
    """

    def __init__(self, kind: str, transport: RTCDtlsTransport) -> None:
        if transport.state == "closed":
            raise InvalidStateError

        self._enabled = True
        self.__active_ssrc: dict[int, datetime.datetime] = {}
        self.__codecs: dict[int, RTCRtpCodecParameters] = {}
        self.__decoder_queue: queue.Queue = queue.Queue()
        self.__decoder_thread: Optional[threading.Thread] = None
        self.__kind = kind
        if kind == "audio":
            self.__jitter_buffer = JitterBuffer(capacity=16, prefetch=4)
            self.__nack_generator = None
            self.__remote_bitrate_estimator = None
            self.__twcc_recorder = None
        else:
            self.__jitter_buffer = JitterBuffer(capacity=128, is_video=True)
            self.__nack_generator = NackGenerator()
            self.__remote_bitrate_estimator = RemoteBitrateEstimator()
            self.__twcc_recorder = None  # Will be initialized when TWCC is enabled
        self._track: Optional[RemoteStreamTrack] = None
        self.__rtcp_exited = asyncio.Event()
        self.__rtcp_started = asyncio.Event()
        self.__rtcp_task: Optional[asyncio.Future[None]] = None
        self.__twcc_exited = asyncio.Event()
        self.__twcc_started = asyncio.Event()
        self.__twcc_task: Optional[asyncio.Future[None]] = None
        self.__rtx_ssrc: dict[int, int] = {}
        self.__started = False
        self.__stats = RTCStatsReport()
        self.__timestamp_mapper = TimestampMapper()
        self.__transport = transport

        # RTCP
        self.__lsr: dict[int, int] = {}
        self.__lsr_time: dict[int, float] = {}
        self.__remote_streams: dict[int, StreamStatistics] = {}
        self.__rtcp_ssrc: Optional[int] = None

        # RX bottleneck debugging instrumentation
        self.__rx_debug = {
            'packets_received': 0,
            'bytes_received': 0,
            'frames_queued': 0,
            'last_report_time': time.time(),
            'last_report_packets': 0,
            'last_report_bytes': 0,
        }

        # logging
        self.__log_debug: Callable[..., None] = lambda *args: None
        if logger.isEnabledFor(logging.DEBUG):
            self.__log_debug = lambda msg, *args: logger.debug(
                f"RTCRtpReceiver(%s) {msg}", self.__kind, *args
            )

    @property
    def track(self) -> MediaStreamTrack:
        """
        The :class:`MediaStreamTrack` which is being handled by the receiver.
        """
        return self._track

    @property
    def transport(self) -> RTCDtlsTransport:
        """
        The :class:`RTCDtlsTransport` over which the media for the receiver's
        track is received.
        """
        return self.__transport

    @classmethod
    def getCapabilities(self, kind: str) -> Optional[RTCRtpCapabilities]:
        """
        Returns the most optimistic view of the system's capabilities for
        receiving media of the given `kind`.

        :rtype: :class:`RTCRtpCapabilities`
        """
        return get_capabilities(kind)

    async def getStats(self) -> RTCStatsReport:
        """
        Returns statistics about the RTP receiver.

        :rtype: :class:`RTCStatsReport`
        """
        for ssrc, stream in self.__remote_streams.items():
            self.__stats.add(
                RTCInboundRtpStreamStats(
                    # RTCStats
                    timestamp=clock.current_datetime(),
                    type="inbound-rtp",
                    id="inbound-rtp_" + str(id(self)),
                    # RTCStreamStats
                    ssrc=ssrc,
                    kind=self.__kind,
                    transportId=self.transport._stats_id,
                    # RTCReceivedRtpStreamStats
                    packetsReceived=stream.packets_received,
                    packetsLost=stream.packets_lost,
                    jitter=stream.jitter,
                    # RTPInboundRtpStreamStats
                )
            )
        self.__stats.update(self.transport._get_stats())

        return self.__stats

    def getSynchronizationSources(self) -> list[RTCRtpSynchronizationSource]:
        """
        Returns a :class:`RTCRtpSynchronizationSource` for each unique SSRC identifier
        received in the last 10 seconds.
        """
        cutoff = clock.current_datetime() - datetime.timedelta(seconds=10)
        sources = []
        for source, timestamp in self.__active_ssrc.items():
            if timestamp >= cutoff:
                sources.append(
                    RTCRtpSynchronizationSource(source=source, timestamp=timestamp)
                )
        return sources

    async def receive(self, parameters: RTCRtpReceiveParameters) -> None:
        """
        Attempt to set the parameters controlling the receiving of media.

        :param parameters: The :class:`RTCRtpParameters` for the receiver.
        """
        if not self.__started:
            for codec in parameters.codecs:
                self.__codecs[codec.payloadType] = codec
            for encoding in parameters.encodings:
                if encoding.rtx:
                    self.__rtx_ssrc[encoding.rtx.ssrc] = encoding.ssrc

            # start decoder thread
            self.__decoder_thread = threading.Thread(
                target=decoder_worker,
                name=self.__kind + "-decoder",
                args=(
                    asyncio.get_event_loop(),
                    self.__decoder_queue,
                    self._track._queue,
                ),
            )
            self.__decoder_thread.start()

            self.__transport._register_rtp_receiver(self, parameters)
            self.__rtcp_task = asyncio.ensure_future(self._run_rtcp())
            self.__started = True

    def setTransport(self, transport: RTCDtlsTransport) -> None:
        self.__transport = transport

    def enable_twcc(self, ssrc: int) -> None:
        """
        Enable Transport-Wide Congestion Control (TWCC) feedback.

        Args:
            ssrc: The media SSRC to track for TWCC feedback
        """
        if self.__twcc_recorder is None:
            from .twcc.receiver import TWCCRecorder

            # CRITICAL: Pass sender_ssrc (our RTCP SSRC). Media SSRC will be auto-detected from RTP packets.
            # This ensures TWCC packets have different SSRCs per RTCP spec
            self.__twcc_recorder = TWCCRecorder(
                sender_ssrc=self.__rtcp_ssrc
            )
            logger.info(f"✅ TWCC enabled with sender_ssrc={self.__rtcp_ssrc}, media_ssrc will be auto-detected")
            self.__log_debug("TWCC enabled with sender SSRC %d (media SSRC will be auto-detected)", self.__rtcp_ssrc)

            # Start dedicated TWCC feedback task with 100ms interval
            if self.__twcc_task is None:
                self.__twcc_task = asyncio.ensure_future(self._run_twcc())
                logger.info("✅ TWCC feedback task started with 100ms interval")
                self.__log_debug("TWCC feedback task started")

    async def stop(self) -> None:
        """
        Irreversibly stop the receiver.
        """
        if self.__started:
            self.__transport._unregister_rtp_receiver(self)
            self.__stop_decoder()

            # shutdown RTCP task
            await self.__rtcp_started.wait()
            self.__rtcp_task.cancel()
            await self.__rtcp_exited.wait()

            # shutdown TWCC task if it was started
            if self.__twcc_task is not None:
                await self.__twcc_started.wait()
                self.__twcc_task.cancel()
                await self.__twcc_exited.wait()

    def _handle_disconnect(self) -> None:
        self.__stop_decoder()

    async def _handle_rtcp_packet(self, packet: AnyRtcpPacket) -> None:
        self.__log_debug("< %s", packet)

        if isinstance(packet, RtcpSrPacket):
            self.__stats.add(
                RTCRemoteOutboundRtpStreamStats(
                    # RTCStats
                    timestamp=clock.current_datetime(),
                    type="remote-outbound-rtp",
                    id=f"remote-outbound-rtp_{id(self)}",
                    # RTCStreamStats
                    ssrc=packet.ssrc,
                    kind=self.__kind,
                    transportId=self.transport._stats_id,
                    # RTCSentRtpStreamStats
                    packetsSent=packet.sender_info.packet_count,
                    bytesSent=packet.sender_info.octet_count,
                    # RTCRemoteOutboundRtpStreamStats
                    remoteTimestamp=clock.datetime_from_ntp(
                        packet.sender_info.ntp_timestamp
                    ),
                )
            )
            self.__lsr[packet.ssrc] = (
                (packet.sender_info.ntp_timestamp) >> 16
            ) & 0xFFFFFFFF
            self.__lsr_time[packet.ssrc] = time.time()
        elif isinstance(packet, RtcpByePacket):
            self.__stop_decoder()

    async def _handle_rtp_packet(self, packet: RtpPacket, arrival_time_us: int, processing_delay_us: int = 0, timing_data: dict = None) -> None:
        """
        Handle an incoming RTP packet.

        Args:
            packet: The RTP packet to handle
            arrival_time_us: Packet arrival time in microseconds (monotonic clock)
            processing_delay_us: Time spent in SRTP unprotect (microseconds)
            timing_data: Optional dict with detailed timing breakdowns
        """
        self.__log_debug("< %s", packet)

        # Import profiler
        from .profiler import get_rtp_profiler
        profiler = get_rtp_profiler()

        # 🔍 PROFILING: Store timing_data for later (will be updated after TWCC recording)
        self.__current_packet_timing = timing_data

        # Track processing delay statistics for event loop analysis
        if not hasattr(self, '__processing_delay_stats'):
            self.__processing_delay_stats = {
                'count': 0,
                'sum': 0,
                'sum_sq': 0,
                'min': float('inf'),
                'max': 0,
                'last_report_time': time.time(),
                'last_report_count': 0
            }

        stats = self.__processing_delay_stats
        stats['count'] += 1
        stats['sum'] += processing_delay_us
        stats['sum_sq'] += processing_delay_us * processing_delay_us
        stats['min'] = min(stats['min'], processing_delay_us)
        stats['max'] = max(stats['max'], processing_delay_us)

        # Report every second
        now = time.time()
        if now - stats['last_report_time'] >= 1.0:
            count = stats['count'] - stats['last_report_count']
            if count > 0:
                avg = (stats['sum'] / stats['count']) if stats['count'] > 0 else 0
                variance = (stats['sum_sq'] / stats['count'] - avg * avg) if stats['count'] > 0 else 0
                stddev = variance ** 0.5 if variance > 0 else 0

                logger.info(f"🔍 PROC_DELAY: {count} pkts/s, "
                           f"μs: min={stats['min']}, max={stats['max']}, "
                           f"avg={avg:.0f}, σ={stddev:.0f}")

                # Reset min/max for next period
                stats['min'] = float('inf')
                stats['max'] = 0
                stats['last_report_time'] = now
                stats['last_report_count'] = stats['count']

        # RX bottleneck instrumentation: Track packet arrival
        if self.__kind == "video":
            self.__rx_debug['packets_received'] += 1
            self.__rx_debug['bytes_received'] += len(packet.payload) + packet.padding_size

            # Report every second
            now = time.time()
            if now - self.__rx_debug['last_report_time'] >= 1.0:
                elapsed = now - self.__rx_debug['last_report_time']
                packets_delta = self.__rx_debug['packets_received'] - self.__rx_debug['last_report_packets']
                bytes_delta = self.__rx_debug['bytes_received'] - self.__rx_debug['last_report_bytes']

                pkt_rate = packets_delta / elapsed
                bitrate_mbps = (bytes_delta * 8) / elapsed / 1_000_000

                decoder_qsize = self.__decoder_queue.qsize()
                # Count non-None packets in jitter buffer
                jitter_depth = sum(1 for p in self.__jitter_buffer._packets if p is not None)

                logger.info(f"🔍 RX: {pkt_rate:.1f} pkt/s, {bitrate_mbps:.2f} Mbps, "
                           f"decoder_q={decoder_qsize}, jitter_buf={jitter_depth}/{self.__jitter_buffer.capacity}, "
                           f"frames_queued={self.__rx_debug['frames_queued']}")

                self.__rx_debug['last_report_time'] = now
                self.__rx_debug['last_report_packets'] = self.__rx_debug['packets_received']
                self.__rx_debug['last_report_bytes'] = self.__rx_debug['bytes_received']

        # Debug logging for first few packets to diagnose TWCC extension
        if not hasattr(self, '_debug_packet_count'):
            self._debug_packet_count = 0
        if self._debug_packet_count < 10:
            with open('/tmp/aiortc_rtp_debug.txt', 'a') as f:
                f.write(f"🔍 RTP packet #{self._debug_packet_count}: SSRC={packet.ssrc}, "
                       f"transport_seq_num={packet.extensions.transport_sequence_number}, "
                       f"abs_send_time={packet.extensions.abs_send_time}, "
                       f"twcc_enabled={self.__twcc_recorder is not None}\n")
            self._debug_packet_count += 1

        # If the receiver is disabled, discard the packet.
        if not self._enabled:
            return

        # feed bitrate estimator (requires milliseconds)
        arrival_time_ms = arrival_time_us // 1000
        if self.__remote_bitrate_estimator is not None:
            if packet.extensions.abs_send_time is not None:
                remb = self.__remote_bitrate_estimator.add(
                    abs_send_time=packet.extensions.abs_send_time,
                    arrival_time_ms=arrival_time_ms,
                    payload_size=len(packet.payload) + packet.padding_size,
                    ssrc=packet.ssrc,
                )
                if self.__rtcp_ssrc is not None and remb is not None:
                    # send Receiver Estimated Maximum Bitrate feedback
                    bitrate_bps = remb[0]
                    logger.debug(f"📤 Sending REMB: {bitrate_bps / 1_000_000:.2f} Mbps (SSRCs: {remb[1]})")
                    rtcp_packet = RtcpPsfbPacket(
                        fmt=RTCP_PSFB_APP,
                        ssrc=self.__rtcp_ssrc,
                        media_ssrc=0,
                        fci=pack_remb_fci(*remb),
                    )
                    await self._send_rtcp(rtcp_packet)

        # Auto-enable TWCC if we receive packets with transport_sequence_number extension
        # This matches Pion/libwebrtc behavior where TWCC is automatically enabled when
        # the extension is negotiated in SDP and packets arrive with the extension.
        if (
            self.__twcc_recorder is None
            and packet.extensions.transport_sequence_number is not None
        ):
            # Generate RTCP SSRC if not already set
            if self.__rtcp_ssrc is None:
                import random
                self.__rtcp_ssrc = random.randint(0, 2**32 - 1)

            with open('/tmp/aiortc_rtp_debug.txt', 'a') as f:
                f.write(f"🔧 Auto-enabling TWCC: detected transport_sequence_number on SSRC={packet.ssrc}, RTCP SSRC={self.__rtcp_ssrc}\n")
            logger.info(f"🔧 Auto-enabling TWCC: detected transport_sequence_number extension on SSRC={packet.ssrc}, using RTCP SSRC={self.__rtcp_ssrc}")
            self.enable_twcc(self.__rtcp_ssrc)

        # record TWCC packet
        if self.__twcc_recorder is not None:
            if packet.extensions.transport_sequence_number is not None:
                # Auto-detect media_ssrc from first RTP packet if not already set
                if self.__twcc_recorder.media_ssrc is None:
                    self.__twcc_recorder.set_media_ssrc(packet.ssrc)

                # 🔍 PROFILING: Measure TWCC recording time
                twcc_record_start_us = int(time.monotonic() * 1_000_000)

                # arrival_time_us is already in microseconds with full precision
                self.__twcc_recorder.record_packet(
                    packet.extensions.transport_sequence_number,
                    arrival_time_us=arrival_time_us
                )

                # 🔍 PROFILING: Add TWCC recording timing to timing_data
                twcc_record_done_us = int(time.monotonic() * 1_000_000)
                twcc_record_delay_us = twcc_record_done_us - twcc_record_start_us

                if timing_data is not None:
                    timing_data['twcc_record_delay_us'] = twcc_record_delay_us
                    timing_data['end_to_end_delay_us'] = twcc_record_done_us - timing_data.get('socket_arrival_us', arrival_time_us)

                # 🔍 PROFILING: Report detailed timing statistics
                # Debug: BEFORE any conditions
                if not hasattr(self, '_profiling_debug_count'):
                    self._profiling_debug_count = 0
                self._profiling_debug_count += 1
                if self._profiling_debug_count == 1:
                    logger.info(f"🔍 PROFILING_DEBUG: First packet - timing_data={timing_data is not None}, kind={self.__kind}")

                if timing_data is not None and self.__kind == "video":
                    if not hasattr(self, '_detailed_timing_stats'):
                        self._detailed_timing_stats = {
                            'count': 0,
                            'recv_delay_sum': 0,
                            'srtp_delay_sum': 0,
                            'parse_delay_sum': 0,
                            'twcc_delay_sum': 0,
                            'e2e_delay_sum': 0,
                            'recv_delay_max': 0,
                            'srtp_delay_max': 0,
                            'parse_delay_max': 0,
                            'twcc_delay_max': 0,
                            'e2e_delay_max': 0,
                            'last_report_time': time.time(),
                        }
                        logger.info(f"🔍 PROFILING_INIT: Stats initialized at time={time.time()}")

                    stats = self._detailed_timing_stats
                    stats['count'] += 1
                    stats['recv_delay_sum'] += timing_data.get('recv_delay_us', 0)
                    stats['srtp_delay_sum'] += timing_data.get('srtp_delay_us', 0)
                    stats['parse_delay_sum'] += timing_data.get('parse_delay_us', 0)
                    stats['twcc_delay_sum'] += timing_data.get('twcc_record_delay_us', 0)
                    stats['e2e_delay_sum'] += timing_data.get('end_to_end_delay_us', 0)
                    stats['recv_delay_max'] = max(stats['recv_delay_max'], timing_data.get('recv_delay_us', 0))
                    stats['srtp_delay_max'] = max(stats['srtp_delay_max'], timing_data.get('srtp_delay_us', 0))
                    stats['parse_delay_max'] = max(stats['parse_delay_max'], timing_data.get('parse_delay_us', 0))
                    stats['twcc_delay_max'] = max(stats['twcc_delay_max'], timing_data.get('twcc_record_delay_us', 0))
                    stats['e2e_delay_max'] = max(stats['e2e_delay_max'], timing_data.get('end_to_end_delay_us', 0))

                    # Report every 2 seconds
                    now = time.time()
                    time_since_last = now - stats['last_report_time']
                    if stats['count'] % 1000 == 0:  # Debug every 1000 packets
                        logger.info(f"🔍 DEBUG_TIMER: count={stats['count']}, time_since_last={time_since_last:.2f}s")
                    if time_since_last >= 2.0:
                        if stats['count'] > 0:
                            avg_recv = stats['recv_delay_sum'] / stats['count']
                            avg_srtp = stats['srtp_delay_sum'] / stats['count']
                            avg_parse = stats['parse_delay_sum'] / stats['count']
                            avg_twcc = stats['twcc_delay_sum'] / stats['count']
                            avg_e2e = stats['e2e_delay_sum'] / stats['count']

                            logger.info(f"🔍 RTP_PATH_TIMING: {stats['count']} pkts, "
                                       f"recv: avg={avg_recv:.1f}μs max={stats['recv_delay_max']}μs, "
                                       f"srtp: avg={avg_srtp:.1f}μs max={stats['srtp_delay_max']}μs, "
                                       f"parse: avg={avg_parse:.1f}μs max={stats['parse_delay_max']}μs, "
                                       f"twcc: avg={avg_twcc:.1f}μs max={stats['twcc_delay_max']}μs, "
                                       f"e2e: avg={avg_e2e:.1f}μs max={stats['e2e_delay_max']}μs")

                            # Reset for next period
                            stats['count'] = 0
                            stats['recv_delay_sum'] = 0
                            stats['srtp_delay_sum'] = 0
                            stats['parse_delay_sum'] = 0
                            stats['twcc_delay_sum'] = 0
                            stats['e2e_delay_sum'] = 0
                            stats['recv_delay_max'] = 0
                            stats['srtp_delay_max'] = 0
                            stats['parse_delay_max'] = 0
                            stats['twcc_delay_max'] = 0
                            stats['e2e_delay_max'] = 0
                            stats['last_report_time'] = now
            else:
                # Log first few times to debug why packets aren't being recorded
                if not hasattr(self, '_twcc_warning_count'):
                    self._twcc_warning_count = 0
                if self._twcc_warning_count < 5:
                    logger.warning(f"⚠️  RTP packet missing transport_sequence_number extension (SSRC={packet.ssrc})")
                    self._twcc_warning_count += 1

        # keep track of sources
        self.__active_ssrc[packet.ssrc] = clock.current_datetime()

        # check the codec is known
        codec = self.__codecs.get(packet.payload_type)
        if codec is None:
            self.__log_debug(
                "x RTP packet with unknown payload type %d", packet.payload_type
            )
            return

        # feed RTCP statistics
        if packet.ssrc not in self.__remote_streams:
            self.__remote_streams[packet.ssrc] = StreamStatistics(codec.clockRate)
        self.__remote_streams[packet.ssrc].add(packet)

        # unwrap retransmission packet
        if is_rtx(codec):
            original_ssrc = self.__rtx_ssrc.get(packet.ssrc)
            if original_ssrc is None:
                self.__log_debug("x RTX packet from unknown SSRC %d", packet.ssrc)
                return

            apt = codec.parameters.get("apt")
            if (
                len(packet.payload) < 2
                or not isinstance(apt, int)
                or apt not in self.__codecs
            ):
                return

            packet = unwrap_rtx(packet, payload_type=apt, ssrc=original_ssrc)
            codec = self.__codecs[apt]

        # send NACKs for any missing any packets
        if self.__nack_generator is not None and self.__nack_generator.add(packet):
            await self._send_rtcp_nack(
                packet.ssrc, sorted(self.__nack_generator.missing)
            )

        # parse codec-specific information
        try:
            if packet.payload:
                packet._data = depayload(codec, packet.payload)  # type: ignore
            else:
                packet._data = b""  # type: ignore
        except ValueError as exc:
            self.__log_debug("x RTP payload parsing failed: %s", exc)
            return

        # try to re-assemble encoded frame
        pli_flag, encoded_frame = self.__jitter_buffer.add(packet)
        # check if the PLI should be sent
        if pli_flag:
            await self._send_rtcp_pli(packet.ssrc)

        # if we have a complete encoded frame, decode it
        if encoded_frame is not None and self.__decoder_thread:
            encoded_frame.timestamp = self.__timestamp_mapper.map(
                encoded_frame.timestamp
            )

            # RX bottleneck instrumentation: Track frame queuing
            if self.__kind == "video":
                self.__rx_debug['frames_queued'] += 1

            self.__decoder_queue.put((codec, encoded_frame))

    async def _run_rtcp(self) -> None:
        self.__log_debug("- RTCP started")
        self.__rtcp_started.set()

        try:
            while True:
                # The interval between RTCP packets is varied randomly over the
                # range [0.5, 1.5] times the calculated interval.
                await asyncio.sleep(0.5 + random.random())

                # RTCP RR
                reports = []
                for ssrc, stream in self.__remote_streams.items():
                    lsr = 0
                    dlsr = 0
                    if ssrc in self.__lsr:
                        lsr = self.__lsr[ssrc]
                        delay = time.time() - self.__lsr_time[ssrc]
                        if delay > 0 and delay < 65536:
                            dlsr = int(delay * 65536)

                    reports.append(
                        RtcpReceiverInfo(
                            ssrc=ssrc,
                            fraction_lost=stream.fraction_lost,
                            packets_lost=stream.packets_lost,
                            highest_sequence=stream.max_seq,
                            jitter=stream.jitter,
                            lsr=lsr,
                            dlsr=dlsr,
                        )
                    )

                if self.__rtcp_ssrc is not None and reports:
                    packet = RtcpRrPacket(ssrc=self.__rtcp_ssrc, reports=reports)
                    await self._send_rtcp(packet)

                # Note: TWCC feedback is now sent in a separate _run_twcc() loop at 100ms intervals

        except asyncio.CancelledError:
            pass

        self.__log_debug("- RTCP finished")
        self.__rtcp_exited.set()

    async def _run_twcc(self) -> None:
        """
        Dedicated TWCC feedback loop running every 100ms.
        This is separate from RTCP RR to ensure timely feedback for GCC.
        Following Pion's implementation pattern.
        """
        self.__log_debug("- TWCC feedback started")
        self.__twcc_started.set()

        # Open file for dumping TWCC packets (for debugging)
        import os
        twcc_dump_path = "/tmp/aiortc_twcc_packets.bin"
        twcc_dump = None
        try:
            twcc_dump = open(twcc_dump_path, "wb")
            logger.info(f"📝 TWCC packet dump: {twcc_dump_path}")
        except Exception as e:
            logger.warning(f"Could not open TWCC dump file: {e}")

        # TWCC feedback timing instrumentation
        feedback_count = 0
        last_feedback_time = None
        feedback_interval_sum = 0.0
        feedback_interval_count = 0

        try:
            while True:
                # 100ms interval (aligned with Pion's implementation)
                await asyncio.sleep(0.1)

                # Generate and send TWCC feedback
                if self.__twcc_recorder is not None:
                    gen_start = time.time()
                    feedback = self.__twcc_recorder.generate_feedback()
                    gen_time_ms = (time.time() - gen_start) * 1000

                    if feedback is not None:
                        feedback_count += 1
                        now = time.time()

                        # Track timing between feedback sends
                        if last_feedback_time is not None:
                            interval_ms = (now - last_feedback_time) * 1000
                            feedback_interval_sum += interval_ms
                            feedback_interval_count += 1

                        # Dump packet to file for analysis
                        if twcc_dump:
                            try:
                                # Write packet length (4 bytes) then packet data
                                twcc_dump.write(len(feedback).to_bytes(4, byteorder='big'))
                                twcc_dump.write(feedback)
                                twcc_dump.flush()
                            except Exception as e:
                                logger.warning(f"Failed to dump TWCC packet: {e}")

                        # Decode packet status count from feedback
                        # TWCC packet format: [header(4)] [sender_ssrc(4)] [media_ssrc(4)] [base_seq(2)] [pkt_status_count(2)] [ref_time(3)] [fb_count(1)] [...]
                        # Packet status count is at bytes 14-15
                        if len(feedback) >= 16:
                            packet_status_count = int.from_bytes(feedback[14:16], 'big')
                            base_seq = int.from_bytes(feedback[12:14], 'big')
                            ref_time_24bit = int.from_bytes(feedback[16:19], 'big')

                            # Report every second (every ~10 feedbacks)
                            if feedback_count % 10 == 0 and feedback_interval_count > 0:
                                avg_interval = feedback_interval_sum / feedback_interval_count
                                logger.info(f"🔍 TWCC_FB: Sent {packet_status_count} pkts in report (base_seq={base_seq}, "
                                           f"ref_time={ref_time_24bit}), gen_time={gen_time_ms:.1f}ms, "
                                           f"avg_interval={avg_interval:.1f}ms, count={feedback_count}")
                                # Reset interval tracking
                                feedback_interval_sum = 0.0
                                feedback_interval_count = 0
                        else:
                            logger.info(f"📡 TWCC: Sending feedback ({len(feedback)} bytes)")

                        await self._send_rtcp_raw(feedback)
                        last_feedback_time = now

        except asyncio.CancelledError:
            pass
        finally:
            if twcc_dump:
                try:
                    twcc_dump.close()
                    logger.info(f"📝 TWCC packet dump closed")
                except Exception:
                    pass

        self.__log_debug("- TWCC feedback finished")
        self.__twcc_exited.set()

    async def _send_rtcp(self, packet: AnyRtcpPacket) -> None:
        self.__log_debug("> %s", packet)
        try:
            await self.transport._send_rtp(bytes(packet))
        except ConnectionError:
            pass

    async def _send_rtcp_raw(self, data: bytes) -> None:
        """Send raw RTCP data (for TWCC feedback)."""
        try:
            await self.transport._send_rtp(data)
        except ConnectionError as e:
            logger.warning(f"⚠️  Failed to send TWCC feedback: ConnectionError - {e}")
        except Exception as e:
            logger.error(f"❌ Unexpected error sending TWCC feedback: {type(e).__name__} - {e}")

    async def _send_rtcp_nack(self, media_ssrc: int, lost: list[int]) -> None:
        """
        Send an RTCP packet to report missing RTP packets.
        """
        if self.__rtcp_ssrc is not None:
            packet = RtcpRtpfbPacket(
                fmt=RTCP_RTPFB_NACK, ssrc=self.__rtcp_ssrc, media_ssrc=media_ssrc
            )
            packet.lost = lost
            await self._send_rtcp(packet)

    async def _send_rtcp_pli(self, media_ssrc: int) -> None:
        """
        Send an RTCP packet to report picture loss.
        """
        if self.__rtcp_ssrc is not None:
            packet = RtcpPsfbPacket(
                fmt=RTCP_PSFB_PLI, ssrc=self.__rtcp_ssrc, media_ssrc=media_ssrc
            )
            await self._send_rtcp(packet)

    def _set_rtcp_ssrc(self, ssrc: int) -> None:
        self.__rtcp_ssrc = ssrc

    def __stop_decoder(self) -> None:
        """
        Stop the decoder thread, which will in turn stop the track.
        """
        if self.__decoder_thread:
            self.__decoder_queue.put(None)
            self.__decoder_thread.join()
            self.__decoder_thread = None
