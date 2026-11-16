"""The camera on WebRTC and in still pictures (`pip install nestor-satellite[video]`: aiortc, with PyAV).

`CameraFeed` shares one `Camera` between the sessions that film and the still pictures asked meanwhile: it opens the
camera when the first needs it and closes it after the last, so the camera is on only while something is sent.
"""

import asyncio
import fractions
import time

import av
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError, MediaStreamTrack

from nestor_satellite.camera import Camera, Frame

CLOCK_RATE = 90_000  # the RTP clock of video
STILL_WAIT_S = 5.0  # a camera gives its first image within this


def video_frame(frame: Frame) -> av.VideoFrame:
    """A camera image as a PyAV frame, rows padded to the plane's stride."""
    image = av.VideoFrame(frame.width, frame.height, frame.format.value)
    plane, row = image.planes[0], frame.width * frame.format.channels
    if plane.line_size == row:
        plane.update(frame.pixels)
    else:
        padding = bytes(plane.line_size - row)
        plane.update(b"".join(frame.pixels[y * row : (y + 1) * row] + padding for y in range(frame.height)))
    return image


def jpeg(image: av.VideoFrame) -> bytes:
    """A still picture of `image`, as JPEG."""
    encoder = av.CodecContext.create("mjpeg", "w")
    encoder.width, encoder.height = image.width, image.height  # ty: ignore[missing-attribute]
    encoder.pix_fmt = "yuvj420p"  # ty: ignore[missing-attribute]
    encoder.time_base = fractions.Fraction(1, 30)
    packets = encoder.encode(image.reformat(format="yuvj420p"))  # ty: ignore[missing-attribute]
    packets += encoder.encode(None)  # ty: ignore[missing-attribute]
    return b"".join(bytes(packet) for packet in packets)


class CameraTrack(MediaStreamTrack):
    """The camera's images as an outgoing WebRTC track, stamped as they come."""

    kind = "video"

    def __init__(self, read: "CameraFeed") -> None:
        super().__init__()
        self._feed = read
        self._started: float | None = None

    async def recv(self) -> av.VideoFrame:
        if self.readyState != "live":
            raise MediaStreamError
        try:
            frame = await self._feed.read()
        except Exception as error:  # the camera stopped: the track ends, and the sessions see it
            self.stop()
            raise MediaStreamError from error
        now = time.monotonic()
        if self._started is None:
            self._started = now
        image = video_frame(frame)
        image.pts, image.time_base = int((now - self._started) * CLOCK_RATE), fractions.Fraction(1, CLOCK_RATE)
        return image


class CameraFeed:
    """One camera for every session that films (`open` / `release`) and for still pictures (`still`)."""

    def __init__(self, camera: Camera) -> None:
        self._camera = camera
        self._users = 0
        self._track: CameraTrack | None = None
        self._relay = MediaRelay()
        self._changing = asyncio.Lock()  # opening and closing the camera
        self._reading = asyncio.Lock()  # one read at a time, and never while it closes

    async def open(self) -> MediaStreamTrack:
        """The camera's video for one more user; opens the camera for the first. Raises when it cannot open."""
        async with self._changing:
            if self._track is not None and self._track.readyState != "live":  # the camera stopped: open it again
                self._track = None
                async with self._reading:
                    await self._camera.close()
            if self._track is None:
                await self._camera.start()
                self._track, self._relay = CameraTrack(self), MediaRelay()
            self._users += 1
            return self._relay.subscribe(self._track, buffered=False)  # the latest image, never a backlog

    async def release(self, track: MediaStreamTrack) -> None:
        """A user is done with `track`; the camera closes after the last."""
        track.stop()
        async with self._changing:
            self._users -= 1
            if self._users or self._track is None:
                return
            self._track.stop()
            self._track = None
            async with self._reading:
                await self._camera.close()

    async def still(self, timeout_s: float = STILL_WAIT_S) -> bytes:
        """A still picture, as JPEG: the camera is on just for it, unless a session films already."""
        track = await self.open()
        try:
            image = await asyncio.wait_for(track.recv(), timeout_s)
        finally:
            await self.release(track)
        assert isinstance(image, av.VideoFrame)
        return jpeg(image)

    async def read(self) -> Frame:
        async with self._reading:
            return await self._camera.read()
