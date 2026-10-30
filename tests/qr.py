"""QR codes as a camera would see them, drawn by an encoder independent of the decoder (segno)."""

import random

import segno

from nestor_satellite import Frame


def blank(width: int = 320, height: int = 240) -> Frame:
    return Frame(width, height, bytes([200]) * width * height)


def qr_frame(text: str, *, width: int = 640, height: int = 480, scale: int = 5, at: tuple[int, int] = (150, 70),
             noise: int = 40, seed: int = 1) -> Frame:  # fmt: skip
    """`text` as a QR code on a grey, noisy, off-centre background, dark modules not quite black."""
    rng = random.Random(seed)
    image = bytearray(rng.randint(150, 150 + noise) for _ in range(width * height))
    matrix = [list(row) for row in segno.make(text, error="m").matrix_iter(scale=scale, border=4)]
    left, top = at
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            image[(top + y) * width + left + x] = rng.randint(10, 50) if dark else rng.randint(215, 245)
    return Frame(width, height, bytes(image))


def y4m(frames: list[Frame]) -> bytes:
    """A raw video (YUV4MPEG2, 4:2:0) of grey frames, as a camera device would stream."""
    width, height = frames[0].width, frames[0].height
    video = bytearray(f"YUV4MPEG2 W{width} H{height} F5:1 Ip A1:1 C420jpeg\n".encode())
    for frame in frames:
        video += b"FRAME\n" + frame.gray + bytes([128]) * (width // 2) * (height // 2) * 2
    return bytes(video)
