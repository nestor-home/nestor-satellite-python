"""Write a Nestor satellite in Python: pair with the core, hold its link, talk in writing, aloud
(WebRTC audio, the `voice` extra) or both, carry out its intents, report observations and update
itself when a person asks, following
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
    UPDATE,
    Behavior,
    Capability,
    Device,
    Expression,
    Manifest,
    PairingMethod,
    ProtocolError,
    Software,
    UpdateState,
)
from nestor_satellite.pypi import PypiUpdater, UpdateFailed
from nestor_satellite.update import Updater

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
    "UPDATE",
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
    "PypiUpdater",
    "RecordingSink",
    "Satellite",
    "SilenceSource",
    "Software",
    "TextDisplay",
    "TokenStore",
    "UpdateFailed",
    "UpdateState",
    "Updater",
    "Voice",
    "normalize_enrollment_code",
    "parse_pairing_uri",
    "read_qr",
]
