import ast
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "nestor_satellite"


def test_the_sdk_never_imports_the_nestor_server() -> None:
    found = []
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            found += [f"{path.name}: {n}" for n in names if n == "nestor" or n.startswith("nestor.")]
    assert not found, found


WITHOUT_VOICE = """
import sys
sys.modules.update(dict.fromkeys(["aiortc", "av"]))  # None: as if the voice extra were not installed
from nestor_satellite import Device, Manifest, RecordingSink, Satellite, TokenStore

def satellite(**options):
    return Satellite("http://n:8471", Manifest(Device("ecran", "tablet", "bureau")), tokens=TokenStore(sys.argv[1]),
                     environ={}, **options)

print(sorted(satellite(text_display=print, text_input=True).manifest.names))
try:
    satellite(sink=RecordingSink())
except RuntimeError as error:
    print(error)
"""


def test_a_satellite_in_writing_needs_no_webrtc_and_audio_says_which_extra_it_needs(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-c", WITHOUT_VOICE, str(tmp_path / "t.json")], capture_output=True, text=True, check=True
    )
    assert done.stdout.splitlines() == [
        "['text.display', 'text.input']",
        "a microphone or a speaker needs `pip install nestor-satellite[voice]`",
    ]


def test_importing_the_sdk_loads_no_webrtc() -> None:
    code = "import sys, nestor_satellite; print(sorted(m for m in ('aiortc', 'av') if m in sys.modules))"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_the_command_without_the_voice_extra_says_which_extra_it_needs(tmp_path: Path) -> None:
    code = (
        "import sys; sys.modules.update(dict.fromkeys(['aiortc', 'av'])); from nestor_satellite.cli import main; "
        f"main(['run', '--core', 'http://n:8471', '--room', 'salon', '--token-file', '{tmp_path / 't.json'}'])"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={"PATH": ""})
    assert (done.returncode, done.stderr.strip()) == (
        1,
        "nestor-satellite: a microphone or a speaker needs `pip install nestor-satellite[voice]`",
    )
