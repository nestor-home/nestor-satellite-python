"""The device's camera: it films in the sessions a person opens in Nestor, and reads pairing QR codes.

A `Satellite` given a camera declares `video.capture` (filmed over WebRTC, the `video` extra) and `enrollment.qr`
(decoded by ZXing-C++, the `qr` extra). It opens the camera only while it waits to be paired, during a session and
for a still picture, and closes it after.

Cameras, each behind its own optional import: `AvCamera` reads any camera FFmpeg knows through PyAV (AVFoundation
on macOS, V4L2 on Linux), `OpenCvCamera` through OpenCV (the `opencv` extra), `PiCamera` the Raspberry Pi camera
module through Picamera2 (installed with the system: `apt install python3-picamera2`). `PatternCamera` films colour
bars and needs nothing.
"""

import asyncio
import importlib
import sys
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PixelFormat(StrEnum):
    """How an image's pixels are laid out, named as FFmpeg names them."""

    GRAY = "gray"  # one grey level per pixel
    RGB = "rgb24"
    BGR = "bgr24"  # what OpenCV and Picamera2's RGB888 give

    @property
    def channels(self) -> int:
        return 1 if self is PixelFormat.GRAY else 3


@dataclass(frozen=True, slots=True)
class Frame:
    """An image, 8 bits per channel, row after row."""

    width: int
    height: int
    pixels: bytes
    format: PixelFormat = PixelFormat.GRAY

    def __post_init__(self) -> None:
        if len(self.pixels) != self.width * self.height * self.format.channels:
            raise ValueError("a frame holds width * height pixels of its format")


class Camera(ABC):
    async def start(self) -> None:
        """Open the camera; nothing to open by default. Raises when it cannot."""
        return None

    async def close(self) -> None:
        return None

    @abstractmethod
    async def read(self) -> Frame:
        """The next image; waits for it."""


def read_qr(frame: Frame) -> list[str]:
    """The texts of the QR codes in `frame`."""
    try:
        import zxingcpp
    except ImportError as error:
        raise RuntimeError("reading QR codes needs `pip install nestor-satellite[qr]`") from error
    layout = {PixelFormat.GRAY: zxingcpp.ImageFormat.Lum, PixelFormat.RGB: zxingcpp.ImageFormat.RGB,
              PixelFormat.BGR: zxingcpp.ImageFormat.BGR}[frame.format]  # fmt: skip
    shape = (frame.height, frame.width) + ((3,) if frame.format.channels == 3 else ())
    pixels = memoryview(frame.pixels).cast("B", shape)  # a buffer: zxing-cpp needs no numpy
    image = zxingcpp.ImageView(pixels, frame.width, frame.height, layout)  # ty: ignore[invalid-argument-type]
    return [
        found.text
        for found in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormats(zxingcpp.BarcodeFormat.QRCode))
    ]


def _optional(module: str, install: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError as error:
        raise RuntimeError(f"this camera needs {install}") from error


class AvCamera(Camera):
    """A camera through FFmpeg: `AvCamera("0", format="avfoundation")` on macOS (the first camera),
    `AvCamera("/dev/video0", format="v4l2")` on Linux. Frames are decoded in a worker thread, in colour."""

    def __init__(self, device: str, *, format: str | None = None, options: Mapping[str, str] | None = None) -> None:
        self._device, self._format, self._options = device, format, dict(options or {})
        self._container: Any = None

    async def start(self) -> None:
        av = _optional("av", "`pip install nestor-satellite[qr]` or `[video]`")

        def opened() -> Any:
            return av.open(self._device, format=self._format, options=self._options)

        self._container = await asyncio.to_thread(opened)

    async def read(self) -> Frame:
        if self._container is None:
            raise RuntimeError("the camera is not started")
        container = self._container
        video = container.streams.video[0]

        def colour() -> Frame:
            while True:
                try:  # a fresh iterator each time: one that raised is spent
                    packet = next(container.demux(video))
                except BlockingIOError:  # a live camera has no new image yet
                    time.sleep(0.005)
                    continue
                if packet.size == 0:  # the end of a recorded stream
                    raise EOFError("the camera stopped")
                if images := packet.decode():
                    break
            image = images[-1].reformat(format=PixelFormat.RGB.value)
            plane, row = image.planes[0], image.width * 3
            data = bytes(plane)
            pixels = b"".join(data[y * plane.line_size : y * plane.line_size + row] for y in range(image.height))
            return Frame(image.width, image.height, pixels, PixelFormat.RGB)

        return await asyncio.to_thread(colour)

    async def close(self) -> None:
        if self._container is not None:
            self._container.close()
            self._container = None


class OpenCvCamera(Camera):
    """A camera through OpenCV (`pip install nestor-satellite[opencv]`): `OpenCvCamera(0)` for the first one (V4L2
    on Linux), or a file or stream address. `size` asks the camera for a resolution, which it may round."""

    def __init__(self, device: int | str = 0, *, size: tuple[int, int] | None = None) -> None:
        self._device, self._size = device, size
        self._capture: Any = None

    async def start(self) -> None:
        cv2 = _optional("cv2", "`pip install nestor-satellite[opencv]`")
        linux_camera = isinstance(self._device, int) and sys.platform.startswith("linux")

        def opened() -> Any:
            capture = cv2.VideoCapture(self._device, cv2.CAP_V4L2 if linux_camera else cv2.CAP_ANY)
            if self._size is not None:
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, self._size[0])
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self._size[1])
            if not capture.isOpened():
                capture.release()
                raise OSError(f"cannot open the camera {self._device!r}")
            return capture

        self._capture = await asyncio.to_thread(opened)

    async def read(self) -> Frame:
        if self._capture is None:
            raise RuntimeError("the camera is not started")
        capture = self._capture

        def grabbed() -> Frame:
            ok, image = capture.read()
            if not ok:
                raise EOFError("the camera stopped")
            height, width = image.shape[:2]
            return Frame(width, height, image.tobytes(), PixelFormat.BGR if image.ndim == 3 else PixelFormat.GRAY)

        return await asyncio.to_thread(grabbed)

    async def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class PiCamera(Camera):
    """The Raspberry Pi camera module through Picamera2 (`apt install python3-picamera2`), at `size`."""

    def __init__(self, *, size: tuple[int, int] = (1280, 720), camera: int = 0) -> None:
        self._size, self._index = size, camera
        self._camera: Any = None

    async def start(self) -> None:
        picamera2 = _optional("picamera2", "Picamera2: `apt install python3-picamera2`")

        def started() -> Any:
            camera = picamera2.Picamera2(self._index)
            camera.configure(camera.create_video_configuration(main={"size": self._size, "format": "RGB888"}))
            camera.start()
            return camera

        self._camera = await asyncio.to_thread(started)

    async def read(self) -> Frame:
        if self._camera is None:
            raise RuntimeError("the camera is not started")
        image = await asyncio.to_thread(self._camera.capture_array, "main")
        height, width = image.shape[:2]
        return Frame(width, height, image[:, :, :3].tobytes(), PixelFormat.BGR)  # RGB888 is laid out B, G, R

    async def close(self) -> None:
        if self._camera is not None:
            camera, self._camera = self._camera, None
            await asyncio.to_thread(camera.stop)
            await asyncio.to_thread(camera.close)


BARS = ((235, 235, 235), (235, 235, 16), (16, 235, 235), (16, 235, 16), (235, 16, 235), (235, 16, 16), (16, 16, 235))


class PatternCamera(Camera):
    """Colour bars sliding by at `fps`: a camera for tests and for trying a satellite without one."""

    def __init__(self, *, size: tuple[int, int] = (320, 240), fps: float = 15.0) -> None:
        width, self._height = size
        bar = -(-width // len(BARS))
        self._row = b"".join(bytes(BARS[x // bar]) for x in range(width))
        self._width, self._interval_s, self._count = width, 1 / fps, 0
        self._next: float | None = None

    async def read(self) -> Frame:
        now = time.monotonic()
        self._next = now if self._next is None else self._next + self._interval_s
        await asyncio.sleep(max(0.0, self._next - now))
        shift = (self._count * 3 * 4) % len(self._row)
        self._count += 1
        row = self._row[shift:] + self._row[:shift]
        return Frame(self._width, self._height, row * self._height, PixelFormat.RGB)
