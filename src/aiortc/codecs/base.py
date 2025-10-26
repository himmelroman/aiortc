from abc import ABCMeta, abstractmethod

from av.frame import Frame
from av.packet import Packet

from ..jitterbuffer import JitterFrame


class Decoder(metaclass=ABCMeta):
    @abstractmethod
    def decode(self, encoded_frame: JitterFrame) -> list[Frame]:
        pass  # pragma: no cover


class Encoder(metaclass=ABCMeta):
    @abstractmethod
    def encode(
        self, frame: Frame, force_keyframe: bool = False
    ) -> tuple[list[bytes], list[Packet], int]:
        """
        Encode a frame.

        Args:
            frame: Frame to encode
            force_keyframe: Force keyframe generation

        Returns:
            Tuple of (payloads, packets, timestamp):
            - payloads: List of RTP payload bytes
            - packets: List of encoded av.Packet objects
            - timestamp: RTP timestamp
        """
        pass  # pragma: no cover

    @abstractmethod
    def pack(self, packet: Packet) -> tuple[list[bytes], int]:
        pass  # pragma: no cover
