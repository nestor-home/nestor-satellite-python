"""Reading the QR code of a pairing URI from camera frames (`enrollment.qr`)."""

import sys
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import av
import pytest

from nestor_satellite import AvCamera, Frame, camera, read_qr

from .qr import blank, qr_frame, y4m

URI = "nestor+http://nestor.local:7860/?code=K7QF2M9X"


def test_the_pairing_uri_is_read_from_a_noisy_off_centre_qr_code() -> None:
    assert read_qr(qr_frame(URI)) == [URI]


def test_a_frame_without_a_qr_code_reads_nothing() -> None:
    assert read_qr(blank()) == []


def test_a_frame_is_width_times_height_grey_levels() -> None:
    with pytest.raises(ValueError, match="width"):
        Frame(2, 2, b"\x00")


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
    monkeypatch.setattr(camera.av, "open", lambda *_args, **_options: LiveCamera())
    lens = AvCamera("0", format="avfoundation")
    await lens.start()
    frame = await lens.read()
    assert (frame.width, frame.height) == (8, 2)
    assert frame.gray[:8] == bytes(range(8)), "rows without the padding FFmpeg aligns them with"
