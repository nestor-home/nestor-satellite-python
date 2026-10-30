"""Write a Nestor satellite in Python: pair with the core, stream audio over WebRTC, carry out its
intents and report observations, following the `nestor.satellite/1` protocol."""

from nestor_satellite.audio import AudioSink, AudioSource, RecordingSink, SilenceSource
from nestor_satellite.body import Body
from nestor_satellite.client import CapabilityNotAllowed, Satellite
from nestor_satellite.pairing import TokenStore
from nestor_satellite.protocol import (
    AUDIO_CAPTURE,
    AUDIO_PLAYBACK,
    EXPRESSION,
    HEAD_MOTION,
    PERSON_DETECTION,
    PROTOCOL,
    SPEECH_ACTIVITY,
    Behavior,
    Capability,
    Device,
    Expression,
    Manifest,
    ProtocolError,
)

__all__ = [
    "AUDIO_CAPTURE",
    "AUDIO_PLAYBACK",
    "EXPRESSION",
    "HEAD_MOTION",
    "PERSON_DETECTION",
    "PROTOCOL",
    "SPEECH_ACTIVITY",
    "AudioSink",
    "AudioSource",
    "Behavior",
    "Body",
    "Capability",
    "CapabilityNotAllowed",
    "Device",
    "Expression",
    "Manifest",
    "ProtocolError",
    "RecordingSink",
    "Satellite",
    "SilenceSource",
    "TokenStore",
]
