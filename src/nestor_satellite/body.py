"""What a driver implements to carry out the core's intents. Every method has a no-op default:
override what the device can do.

Calls come from the network loop and must return at once: keep the state, animate elsewhere.
"""

from nestor_satellite.protocol import Behavior, Expression


class Body:
    def behavior(self, behavior: Behavior) -> None:
        """The assistant's attitude changed (idle, listening, thinking, speaking): show it until the next."""

    def expression(self, expression: Expression, intensity: float) -> None:
        """Perform an expression now: it arrives as the matching audio is played."""

    def beat(self) -> None:
        """A word is being played: a light speech accent."""

    def heartbeat(self) -> None:
        """The core was just heard from (a message or audio). A watchdog can rest the device when
        heartbeats stop."""

    def connected(self, up: bool) -> None:
        """The link to the core came up (welcomed) or went down."""
