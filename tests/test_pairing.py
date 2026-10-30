import json
import stat
from pathlib import Path

import httpx
import pytest

from nestor_satellite import Device, Manifest, TokenStore
from nestor_satellite.pairing import pair, show_code

CORE = "http://nestor.local:7860"
MANIFEST = Manifest.of(Device("enceinte-salon", "raspberry-pi-4", "salon"), "audio.capture")


def test_the_token_is_readable_by_its_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "config" / "salon.json"
    path.parent.mkdir()
    path.write_text("{}")
    path.chmod(0o644)  # left more open by someone else
    store = TokenStore(path)
    store.save(CORE, "enceinte-salon", "nst1_secret")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load(CORE, "enceinte-salon") == "nst1_secret"


@pytest.mark.parametrize(("core", "device"), [("http://other:7860", "enceinte-salon"), (CORE, "reachy-salon")])
def test_a_token_for_another_core_or_device_is_not_used(core: str, device: str, tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    store.save(CORE, "enceinte-salon", "nst1_secret")
    assert store.load(core, device) is None


def test_a_missing_or_corrupt_token_file_means_no_token(tmp_path: Path) -> None:
    store = TokenStore(tmp_path / "t.json")
    assert store.load(CORE, "enceinte-salon") is None
    store.path.write_text("not json")
    assert store.load(CORE, "enceinte-salon") is None
    store.forget()
    store.forget()
    assert not store.path.exists()


class Core:
    """A pairing endpoint that answers from a script of poll statuses."""

    def __init__(self, *statuses: str) -> None:
        self.statuses = list(statuses)
        self.requests = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            self.requests += 1
            assert json.loads(request.content)["device"]["id"] == "enceinte-salon"
            return httpx.Response(201, json={"pairing_id": f"p{self.requests}", "code": f"00000{self.requests}"})
        status = self.statuses.pop(0)
        return httpx.Response(200, json={"status": status} | ({"token": "nst1_ok"} if status == "approved" else {}))


async def test_the_code_is_shown_and_the_token_returned_once_approved() -> None:
    core, codes = Core("pending", "pending", "approved"), []
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.handle), base_url=CORE) as http:
        token = await pair(http, MANIFEST, on_code=codes.append, poll_s=0)
    assert (token, codes) == ("nst1_ok", ["000001"])


async def test_an_expired_code_is_replaced_by_a_new_one() -> None:
    core, codes = Core("pending", "expired", "approved"), []
    async with httpx.AsyncClient(transport=httpx.MockTransport(core.handle), base_url=CORE) as http:
        await pair(http, MANIFEST, on_code=codes.append, poll_s=0)
    assert codes == ["000001", "000002"]


def test_the_default_code_display_tells_how_to_approve(capsys: pytest.CaptureFixture[str]) -> None:
    show_code("123456")
    assert "nestor satellites approve 123456" in capsys.readouterr().out
