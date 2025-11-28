import logging
from typing import Optional, Union

from .codecs import get_capabilities
from .rtcrtpparameters import (
    RTCRtpCodecCapability,
    RTCRtpCodecParameters,
    RTCRtpHeaderExtensionParameters,
)
from .rtcrtpreceiver import RTCRtpReceiver
from .rtcrtpsender import RTCRtpSender
from .sdp import DIRECTIONS

logger = logging.getLogger(__name__)


def sort_codecs_by_mime_types(
    codecs: list[RTCRtpCodecCapability], preferred_order: list[str]
) -> list[RTCRtpCodecCapability]:
    """
    Sort codecs by MIME type preference order.

    This is a helper function that mimics the Chrome WebRTC API pattern
    for codec preference management. It reorders the codecs list to match
    the preferred order specified by MIME types.

    :param codecs: List of codec capabilities to sort
    :param preferred_order: List of MIME types in decreasing order of preference
                            (e.g., ["video/VP9", "video/VP8", "video/H264"])
    :return: Sorted list of codec capabilities

    Example::

        from aiortc import RTCRtpSender
        from aiortc.rtcrtptransceiver import sort_codecs_by_mime_types

        # Get all video codecs
        capabilities = RTCRtpSender.getCapabilities("video")

        # Prefer VP9, then VP8, then H264
        preferred = sort_codecs_by_mime_types(
            capabilities.codecs,
            ["video/VP9", "video/VP8", "video/H264"]
        )

        # Set the preferences on a transceiver
        transceiver.setCodecPreferences(preferred)
    """

    def get_preference_index(codec: RTCRtpCodecCapability) -> int:
        try:
            return preferred_order.index(codec.mimeType)
        except ValueError:
            return len(preferred_order)  # Put non-preferred codecs at the end

    return sorted(codecs, key=get_preference_index)


class RTCRtpTransceiver:
    """
    The RTCRtpTransceiver interface describes a permanent pairing of an
    :class:`RTCRtpSender` and an :class:`RTCRtpReceiver`, along with some
    shared state.
    """

    def __init__(
        self,
        kind: str,
        receiver: RTCRtpReceiver,
        sender: RTCRtpSender,
        direction: str = "sendrecv",
    ):
        self.__currentDirection: Optional[str] = None
        self.__direction = direction
        self.__kind = kind
        self.__mid: Optional[str] = None
        self.__mline_index: Optional[int] = None
        self.__receiver = receiver
        self.__sender = sender
        self.__stopped = False

        self._offerDirection: Optional[str] = None
        self._preferred_codecs: list[RTCRtpCodecCapability] = []

        # FIXME: this is only used by RTCPeerConnection
        self._bundled = False
        self._codecs: list[RTCRtpCodecParameters] = []
        self._headerExtensions: list[RTCRtpHeaderExtensionParameters] = []

    @property
    def currentDirection(self) -> Optional[str]:
        """
        The currently negotiated direction of the transceiver.

        One of `'sendrecv'`, `'sendonly'`, `'recvonly'`, `'inactive'` or `None`.
        """
        return self.__currentDirection

    @property
    def direction(self) -> str:
        """
        The preferred direction of the transceiver, which will be used in
        :meth:`RTCPeerConnection.createOffer` and
        :meth:`RTCPeerConnection.createAnswer`.

        One of `'sendrecv'`, `'sendonly'`, `'recvonly'` or `'inactive'`.
        """
        return self.__direction

    @direction.setter
    def direction(self, direction: str) -> None:
        assert direction in DIRECTIONS
        self.__direction = direction

    @property
    def kind(self) -> str:
        return self.__kind

    @property
    def mid(self) -> Optional[str]:
        return self.__mid

    @property
    def receiver(self) -> RTCRtpReceiver:
        """
        The :class:`RTCRtpReceiver` that handles receiving and decoding
        incoming media.
        """
        return self.__receiver

    @property
    def sender(self) -> RTCRtpSender:
        """
        The :class:`RTCRtpSender` responsible for encoding and sending
        data to the remote peer.
        """
        return self.__sender

    @property
    def stopped(self) -> bool:
        return self.__stopped

    def setCodecPreferences(
        self, codecs: Union[list[RTCRtpCodecCapability], list[str]]
    ) -> None:
        """
        Override the default codec preferences.

        See :meth:`RTCRtpSender.getCapabilities` and
        :meth:`RTCRtpReceiver.getCapabilities` for the supported codecs.

        :param codecs: Either a list of :class:`RTCRtpCodecCapability` objects in
                       decreasing order of preference, OR a list of MIME type strings
                       (e.g., ["video/VP9", "video/VP8"]). If empty, restores the
                       default preferences.

        Example (using MIME type strings - simpler API)::

            # Prefer VP9, then fall back to VP8
            transceiver.setCodecPreferences(["video/VP9", "video/VP8"])

        Example (using RTCRtpCodecCapability objects - advanced API)::

            capabilities = RTCRtpSender.getCapabilities("video")
            vp9_codecs = [c for c in capabilities.codecs if c.mimeType == "video/VP9"]
            transceiver.setCodecPreferences(vp9_codecs)
        """
        if not codecs:
            self._preferred_codecs = []
            return

        capabilities = get_capabilities(self.kind).codecs

        # If the input is a list of strings (MIME types), convert to codec list
        if codecs and isinstance(codecs[0], str):
            mime_types = codecs
            # Sort all capabilities by the preferred MIME type order
            sorted_codecs = sort_codecs_by_mime_types(capabilities, mime_types)
            # Filter to only include the requested MIME types
            codecs = [c for c in sorted_codecs if c.mimeType in mime_types]

        unique: list[RTCRtpCodecCapability] = []
        for codec in reversed(codecs):
            if codec not in capabilities:
                raise ValueError("Codec is not in capabilities")
            if codec not in unique:
                unique.insert(0, codec)
        self._preferred_codecs = unique

    async def stop(self) -> None:
        """
        Permanently stops the :class:`RTCRtpTransceiver`.
        """
        await self.__receiver.stop()
        await self.__sender.stop()
        self.__stopped = True

    def _setCurrentDirection(self, direction: str) -> None:
        self.__currentDirection = direction

        if direction == "sendrecv":
            self.__sender._enabled = True
            self.__receiver._enabled = True
        elif direction == "sendonly":
            self.__sender._enabled = True
            self.__receiver._enabled = False
        elif direction == "recvonly":
            self.__sender._enabled = False
            self.__receiver._enabled = True
        elif direction == "inactive":
            self.__sender._enabled = False
            self.__receiver._enabled = False

    def _set_mid(self, mid: str) -> None:
        self.__mid = mid

    def _get_mline_index(self) -> Optional[int]:
        return self.__mline_index

    def _set_mline_index(self, idx: int) -> None:
        self.__mline_index = idx
