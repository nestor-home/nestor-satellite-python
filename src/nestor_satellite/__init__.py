"""Write a Nestor satellite in Python: pair with the core, hold its link, talk in writing, aloud
(WebRTC audio, the `voice` extra) or both, carry out its intents and report observations, following
the `nestor.satellite/1` protocol."""

from nestor_satellite.audio import AudioSink, AudioSource, RecordingSink, SilenceSource
from nestor_satellite.body import Body
from nestor_satellite.camera import AvCamera, Camera, Frame, read_qr
from nestor_satellite.client import CapabilityNotAllowed, Satellite
from nestor_satellite.codes import Enrollment, InvalidEnrollment, normalize_enrollment_code, parse_pairing_uri
from nestor_satellite.dispatch import TextDisplay
from nestor_satellite.pairing import (
    PAIRING_CODE,
    PAIRING_URI,
    Display,
    EnrollmentOutcome,
    PairingFailed,
    TokenStore,
    Voice,
)
from nestor_satellite.protocol import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    EXPRESSION,
    HEAD_MOTION,
    IDENTIFY,
    PERSON_DETECTION,
    PROTOCOL,
    SPEECH_ACTIVITY,
    TEXT_DISPLAY,
    TEXT_INPUT,
    Behavior,
    Capability,
    Device,
    Expression,
    Manifest,
    PairingMethod,
    ProtocolError,
)

__all__ = [
    "AUDIO_CAPTURE",
    "AUDIO_PLAYBACK",
    "EXPRESSION",
    "HEAD_MOTION",
    "IDENTIFY",
    "PAIRING_CODE",
    "PAIRING_URI",
    "PERSON_DETECTION",
    "PROTOCOL",
    "SPEECH_ACTIVITY",
    "TEXT_DISPLAY",
    "TEXT_INPUT",
    "AudioSink",
    "AudioSource",
    "AvCamera",
    "Behavior",
    "Body",
    "Camera",
    "Capability",
    "CapabilityNotAllowed",
    "Device",
    "Display",
    "Enrollment",
    "EnrollmentOutcome",
    "Expression",
    "Frame",
    "InvalidEnrollment",
    "Manifest",
    "PairingFailed",
    "PairingMethod",
    "ProtocolError",
    "RecordingSink",
    "Satellite",
    "SilenceSource",
    "TextDisplay",
    "TokenStore",
    "Voice",
    "normalize_enrollment_code",
    "parse_pairing_uri",
    "read_qr",
]
