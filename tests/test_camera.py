"""The cameras a satellite films with, and the QR code of a pairing URI read from their frames (`enrollment.qr`)."""

import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import av
import pytest

from nestor_satellite import AvCamera, Frame, OpenCvCamera, PatternCamera, PiCamera, PixelFormat, read_qr

from .qr import blank, qr_frame, y4m

URI = "nestor+http://nestor.local:8471/?code=K7QF2M9X"


def test_the_pairing_uri_is_read_from_a_noisy_off_centre_qr_code() -> None:
    assert read_qr(qr_frame(URI)) == [URI]


def test_a_frame_without_a_qr_code_reads_nothing() -> None:
    assert read_qr(blank()) == []


def test_a_frame_is_width_times_height_pixels_of_its_format() -> None:
    with pytest.raises(ValueError, match="width"):
        Frame(2, 2, b"\x00")
    with pytest.raises(ValueError, match="width"):
        Frame(2, 2, bytes(4), PixelFormat.RGB)


def test_without_the_decoder_it_says_what_to_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "zxingcpp", None)
    with pytest.raises(RuntimeError, match=r"nestor-satellite\[qr\]"):
        read_qr(blank())


async def test_a_camera_stream_is_read_frame_by_frame_until_it_ends(tmp_path) -> None:
    video = tmp_path / "camera.y4m"
    video.write_bytes(y4m([qr_frame(URI, width=320, height=240, scale=4, at=(40, 20)), blank()]))
    camera = AvCamera(str(video), format="yuv4mpegpipe")
    with pytest.raises(RuntimeError, match="not started"):
        await camera.read()
    await camera.start()
    try:
        assert read_qr(await camera.read()) == [URI]
        assert read_qr(await camera.read()) == []
        with pytest.raises(EOFError):
            await camera.read()
    finally:
        await camera.close()
        await camera.close()


class LiveCamera:
    """What FFmpeg gives for a live camera: no image yet, then a packet without one, then an image."""

    def __init__(self) -> None:
        image = av.VideoFrame(8, 2, "gray")
        image.planes[0].update(bytes(range(16)) + bytes(image.planes[0].buffer_size - 16))
        self.answers: list[Any] = [BlockingIOError(), SimpleNamespace(size=3, decode=lambda: []),
                                   SimpleNamespace(size=3, decode=lambda: [image])]  # fmt: skip
        self.streams = SimpleNamespace(video=["video"])

    def demux(self, _stream: object) -> Iterator[Any]:
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        yield answer

    def close(self) -> None: ...


async def test_a_live_camera_is_waited_for_until_it_has_an_image(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(av, "open", lambda *_args, **_options: LiveCamera())
    lens = AvCamera("0", format="avfoundation")
    await lens.start()
    frame = await lens.read()
    assert (frame.width, frame.height) == (8, 2)
    grey_as_colour = bytes(level for level in range(8) for _ in range(3))
    assert frame.pixels[:24] == grey_as_colour, "rows in colour, without the padding FFmpeg aligns them with"


async def test_the_pattern_camera_films_colour_bars_that_move_at_its_pace() -> None:
    camera = PatternCamera(size=(70, 2), fps=50)
    started = time.monotonic()
    frames = [await camera.read() for _ in range(6)]
    assert time.monotonic() - started >= 5 / 50 * 0.9, "paced, not as fast as it can"
    first, second = frames[0], frames[1]
    assert (first.width, first.height, first.format) == (70, 2, PixelFormat.RGB)
    assert first.pixels[:3] != first.pixels[-3:], "bars of several colours"
    assert first.pixels != second.pixels, "moving"


async def test_an_opencv_camera_reads_images_in_colour_until_the_stream_ends(tmp_path: Path) -> None:
    import cv2
    import numpy as np

    path = str(tmp_path / "camera.avi")
    writer = cv2.VideoWriter(path, cv2.VideoWriter.fourcc(*"MJPG"), 5, (64, 48))
    for level in (40, 200):
        writer.write(np.full((48, 64, 3), (level, 0, 255 - level), dtype=np.uint8))
    writer.release()
    camera = OpenCvCamera(path)
    with pytest.raises(RuntimeError, match="not started"):
        await camera.read()
    await camera.start()
    try:
        frame = await camera.read()
        assert (frame.width, frame.height, frame.format) == (64, 48, PixelFormat.BGR)
        blue, _, red = frame.pixels[:3]
        assert abs(blue - 40) < 10 and abs(red - 215) < 10, "OpenCV's own order: blue, green, red"
        await camera.read()
        with pytest.raises(EOFError):
            await camera.read()
    finally:
        await camera.close()
        await camera.close()


async def test_an_opencv_camera_that_does_not_open_says_so() -> None:
    camera = OpenCvCamera(str(Path("/nonexistent/camera.avi")))
    with pytest.raises(OSError, match="cannot open"):
        await camera.start()


class FakePicamera2:
    """Picamera2 as the Raspberry Pi gives it: configured for RGB888 (laid out B, G, R), started, then captured."""

    instances: ClassVar[list["FakePicamera2"]] = []

    def __init__(self, index: int) -> None:
        self.index, self.config, self.calls = index, None, []
        FakePicamera2.instances.append(self)

    def create_video_configuration(self, main: dict[str, Any]) -> dict[str, Any]:
        return {"main": main}

    def configure(self, config: dict[str, Any]) -> None:
        self.config = config

    def start(self) -> None:
        self.calls.append("start")

    def capture_array(self, stream: str) -> Any:
        import numpy as np

        width, height = self.config["main"]["size"] if self.config else (0, 0)
        return np.full((height, width, 3), (10, 20, 30), dtype=np.uint8)

    def stop(self) -> None:
        self.calls.append("stop")

    def close(self) -> None:
        self.calls.append("close")


async def test_the_raspberry_pi_camera_films_through_picamera2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=FakePicamera2))
    camera = PiCamera(size=(32, 24))
    await camera.start()
    frame = await camera.read()
    await camera.close()
    [pi] = FakePicamera2.instances
    assert pi.config == {"main": {"size": (32, 24), "format": "RGB888"}}
    assert (frame.width, frame.height, frame.format, frame.pixels[:3]) == (32, 24, PixelFormat.BGR, bytes([10, 20, 30]))
    assert pi.calls == ["start", "stop", "close"]


@pytest.mark.parametrize(
    ("camera", "module", "install"),
    [(lambda: PiCamera(), "picamera2", "python3-picamera2"), (lambda: OpenCvCamera(), "cv2", r"\[opencv\]")],
)
async def test_a_camera_without_its_library_says_what_to_install(
    monkeypatch: pytest.MonkeyPatch, camera: Any, module: str, install: str
) -> None:
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(RuntimeError, match=install):
        await camera().start()


def test_an_image_whose_rows_ffmpeg_pads_keeps_its_pixels() -> None:
    from nestor_satellite.video import video_frame

    pixels = bytes(range(30)) * 2  # 10 pixels wide: FFmpeg aligns its rows on more
    image = video_frame(Frame(10, 2, pixels, PixelFormat.RGB))
    plane = image.planes[0]
    assert plane.line_size > 30
    rows = bytes(plane)
    assert rows[:30] + rows[plane.line_size : plane.line_size + 30] == pixels
