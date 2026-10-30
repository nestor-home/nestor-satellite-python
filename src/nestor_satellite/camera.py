"""Enrollment by QR code (`enrollment.qr`): the device's camera, and the codes read from its frames.

A `Satellite` given a camera declares `enrollment.qr`, opens the camera only while it waits to be
paired, and sends the pairing URI of any QR code it reads. Decoding is ZXing-C++
(`pip install nestor-satellite[qr]`); `AvCamera` reads any camera FFmpeg knows (AVFoundation on
macOS, V4L2 on Linux) through PyAV, already a dependency of the WebRTC stack.
"""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import av


@dataclass(frozen=True, slots=True)
class Frame:
    """An image as 8-bit grey levels, row after row."""

    width: int
    height: int
    gray: bytes

    def __post_init__(self) -> None:
        if len(self.gray) != self.width * self.height:
            raise ValueError("a frame holds width * height grey levels")


class Camera(ABC):
    async def start(self) -> None:
        """Open the camera; nothing to open by default. Raises when it cannot."""
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def read(self) -> Frame:
        """The latest image; waits for one."""


def read_qr(frame: Frame) -> list[str]:
    """The texts of the QR codes in `frame`."""
    try:
        import zxingcpp
    except ImportError as error:
        raise RuntimeError("reading QR codes needs `pip install nestor-satellite[qr]`") from error
    image = memoryview(frame.gray).cast("B", (frame.height, frame.width))
    return [
        found.text
        for found in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormats(zxingcpp.BarcodeFormat.QRCode))
    ]


class AvCamera(Camera):
    """A camera through FFmpeg: `AvCamera("0", format="avfoundation")` on macOS (the first camera),
    `AvCamera("/dev/video0", format="v4l2")` on Linux. Frames are decoded in a worker thread."""

    def __init__(self, device: str, *, format: str | None = None, options: Mapping[str, str] | None = None) -> None:
        self._device, self._format, self._options = device, format, dict(options or {})
        self._container: Any = None
        self._frames: Iterator[Any] | None = None

    async def start(self) -> None:
        def opened() -> Any:
            return av.open(self._device, format=self._format, options=self._options)

        self._container = await asyncio.to_thread(opened)
        self._frames = self._container.decode(video=0)

    async def read(self) -> Frame:
        if self._frames is None:
            raise RuntimeError("the camera is not started")
        frames = self._frames

        def grey() -> Frame:
            try:
                image = next(frames).reformat(format="gray")
            except StopIteration:  # a future cannot carry it
                raise EOFError("the camera stopped") from None
            plane, width = image.planes[0], image.width
            rows = bytes(plane)
            gray = b"".join(rows[y * plane.line_size : y * plane.line_size + width] for y in range(image.height))
            return Frame(width, image.height, gray)

        return await asyncio.to_thread(grey)

    async def close(self) -> None:
        if self._container is not None:
            self._container.close()
            self._container = self._frames = None
