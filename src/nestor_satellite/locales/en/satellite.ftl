## nestor-satellite: what a satellite says to people while it pairs and connects.

# The device code, on a screen next to the code itself.
device-code-shown = Enter this code in Nestor to pair this device.
# The device code, in the log.
device-code-logged = Pairing code { $code }: enter it in Nestor to pair this device.
# The device code, aloud: $digits are read one by one. The bundled voice plays a recorded clip for
# each piece of text between the digits (scripts/voice.py records them): rerun it after changing this.
device-code-spoken = Pairing code: { $digits }. I repeat: { $digits }.
# A device with neither screen nor voice.
device-code-headless = This device has no screen and no voice: to pair it, add it in Nestor with an enrollment code and set NESTOR_PAIRING_URI.
device-code-expired = The pairing code expired: asking for a new one.

enrollment-refused = { $reason ->
    [expired] This enrollment code has expired.
    [used] This enrollment code has already been used.
    [cancelled] This enrollment code was cancelled.
   *[unknown] This enrollment code is not valid.
}
enrollment-invalid = This is not an enrollment code.
enrollment-misconfigured = { $source } is not a pairing URI or an enrollment code.
enrollment-not-pending = This device was already paired with its code: the enrollment code was not used.
enrollment-accepted = Enrollment code accepted.
camera-failed = The camera is unavailable ({ $error }): pair this device another way.

paired = Paired with Nestor.
pairing-rate-limited = Too many attempts: trying again in { $seconds } s.
pairing-busy = Too many devices are waiting to pair: trying again in { $seconds } s.
protocol-unsupported = Nestor does not speak this device's protocol version: update the device or Nestor.
pairing-rejected = Nestor rejected the pairing request ({ $detail }): update this device.
pairing-not-found = No Nestor pairing service at { $core }: check the address and that Nestor is up to date.
no-core = Nestor's address is missing: give it, or a pairing URI (NESTOR_PAIRING_URI).

token-refused = Nestor no longer knows this device: pairing again.
link-failed = Link to Nestor failed ({ $error }): retrying in { $seconds } s.
link-closed = Nestor closed the link: reconnecting in { $seconds } s.
link-off-contract = Nestor refused this device's greeting: retrying in { $seconds } s.
link-replaced = Another connection of this device to Nestor took over: this one stops.
audio-failed = Audio with Nestor failed ({ $error }): retrying in { $seconds } s.
text-not-sent = Not connected to Nestor: the text was not sent.
text-not-allowed = Nestor did not allow typing on this device.
text-too-long = Too long to send: { $max } characters at most.
