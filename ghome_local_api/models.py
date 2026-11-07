"""Typed replies of the local API, read with the rules ha-google-home applies to them."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import math
from typing import Final, Self, override

from .const import JSON_ALARM, JSON_ALARM_VOLUME, JSON_NOTIFICATIONS_ENABLED, JSON_TIMER

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = Mapping[str, JsonValue]

# The fields of a device's build info that are kept: its versions and release channel
BUILD_INFO_FIELDS: Final = (
    "cast_build_revision",
    "system_build_number",
    "release_track",
    "preview_channel_state",
)


def _number(value: JsonValue) -> int | float | None:
    """Return a JSON number that is whole or finite, else None.

    True and false aren't numbers. A whole number isn't turned into a float to check
    it, so one too large to convert is left for its conversion to reject.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _whole(value: JsonValue) -> int | None:
    """Return a JSON whole number, but not true or false, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_all[T](
    items: list[JsonValue], parse: Callable[[JsonValue], T | None]
) -> tuple[T, ...] | None:
    """Return every item parsed, or None if any can't be."""
    parsed = []
    for item in items:
        if (value := parse(item)) is None:
            return None
        parsed.append(value)
    return tuple(parsed)


class AlarmStatus(Enum):
    """The status of an alarm."""

    NONE = 0
    SET = 1
    RINGING = 2
    SNOOZED = 3
    INACTIVE = 4
    MISSED = 5
    # A status this package doesn't know, such as one that newer firmware adds
    UNKNOWN = -1

    @classmethod
    @override
    def _missing_(cls, value: object) -> Self:
        """Return UNKNOWN for a status this package doesn't know."""
        return cls.UNKNOWN


class TimerStatus(Enum):
    """The status of a timer."""

    NONE = 0
    SET = 1
    PAUSED = 2
    RINGING = 3
    # A status this package doesn't know, such as one that newer firmware adds
    UNKNOWN = -1

    @classmethod
    @override
    def _missing_(cls, value: object) -> Self:
        """Return UNKNOWN for a status this package doesn't know."""
        return cls.UNKNOWN


@dataclass(frozen=True, slots=True, kw_only=True)
class Alarm:
    """An alarm, as the device sent it."""

    id: str
    # In milliseconds since the epoch
    fire_time: int | float
    status: AlarmStatus
    label: JsonValue
    recurrence: JsonValue

    @classmethod
    def from_json(cls, item: JsonValue) -> Self | None:
        """Return the alarm, or None if it lacks its ID, a whole-number status or a fire time.

        A missing label or recurrence is None.
        """
        if not isinstance(item, dict):
            return None
        alarm_id, status = item.get("id"), _whole(item.get("status"))
        fire_time = _number(item.get("fire_time"))
        if not isinstance(alarm_id, str) or status is None or fire_time is None:
            return None
        return cls(
            id=alarm_id,
            fire_time=fire_time,
            status=AlarmStatus(status),
            label=item.get("label"),
            recurrence=item.get("recurrence"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Timer:
    """A timer, as the device sent it."""

    id: str
    # In milliseconds
    original_duration: int | float
    # In milliseconds since the epoch; a paused timer may have none
    fire_time: int | float | None
    # In milliseconds
    remaining_duration: int | float | None
    status: TimerStatus
    label: JsonValue

    @classmethod
    def from_json(cls, item: JsonValue) -> Self | None:
        """Return the timer, or None if it lacks its ID, a whole-number status or its duration.

        A fire time or remaining duration may be missing or null, which gives None,
        but not another value that isn't a number.
        """
        if not isinstance(item, dict):
            return None
        timer_id, status = item.get("id"), _whole(item.get("status"))
        duration = _number(item.get("original_duration"))
        if not isinstance(timer_id, str) or status is None or duration is None:
            return None
        fire_time, remaining = item.get("fire_time"), item.get("remaining_duration")
        if any(
            value is not None and _number(value) is None
            for value in (fire_time, remaining)
        ):
            return None
        return cls(
            id=timer_id,
            original_duration=duration,
            fire_time=_number(fire_time),
            remaining_duration=_number(remaining),
            status=TimerStatus(status),
            label=item.get("label"),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class AlarmsAndTimers:
    """A device's alarms and timers, with the reply they came in."""

    alarms: tuple[Alarm, ...]
    timers: tuple[Timer, ...]
    raw: JsonValue

    @classmethod
    def from_json(cls, body: JsonValue) -> Self | None:
        """Return the alarms and timers of a reply, or None if it can't be read whole.

        The reply needs a list of alarms and a list of timers, with every item
        readable, so a reply is never read only in part.
        """
        if not isinstance(body, dict):
            return None
        alarms, timers = body.get(JSON_ALARM), body.get(JSON_TIMER)
        if not isinstance(alarms, list) or not isinstance(timers, list):
            return None
        parsed_alarms = _parse_all(alarms, Alarm.from_json)
        parsed_timers = _parse_all(timers, Timer.from_json)
        if parsed_alarms is None or parsed_timers is None:
            return None
        return cls(alarms=parsed_alarms, timers=parsed_timers, raw=body)


@dataclass(frozen=True, slots=True, kw_only=True)
class DeleteResult:
    """The reply to a request to delete alarms or timers."""

    raw: JsonValue

    @property
    def confirmed(self) -> bool:
        """Return whether the reply confirms the delete: only {"success": true} does."""
        return isinstance(self.raw, dict) and self.raw.get("success") is True


@dataclass(frozen=True, slots=True, kw_only=True)
class AlarmVolumeReply:
    """The reply to a request for the alarm volume or to set it; any JSON value."""

    raw: JsonValue

    @property
    def has_volume(self) -> bool:
        """Return whether the reply is an object with a volume."""
        return isinstance(self.raw, dict) and JSON_ALARM_VOLUME in self.raw

    def percent(self) -> int:
        """Return the volume as a whole percentage, from the fraction the device sends.

        Raise ValueError if the reply has no volume or it isn't a number, such as
        "loud" or NaN, and OverflowError if it is infinite.
        """
        if not isinstance(self.raw, dict) or JSON_ALARM_VOLUME not in self.raw:
            raise ValueError("The reply has no alarm volume")
        return round(float(str(self.raw[JSON_ALARM_VOLUME])) * 100)


@dataclass(frozen=True, slots=True, kw_only=True)
class DoNotDisturbReply:
    """The reply to a request for the Do Not Disturb setting or to set it; any JSON value."""

    raw: JsonValue

    @property
    def enabled(self) -> bool | None:
        """Return whether Do Not Disturb is on, or None if the reply doesn't say.

        The device sends whether notifications are on, the opposite.
        """
        if isinstance(self.raw, dict) and JSON_NOTIFICATIONS_ENABLED in self.raw:
            return not bool(self.raw[JSON_NOTIFICATIONS_ENABLED])
        return None


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceInfo:
    """What a device reports about itself without a token."""

    # Google's ID for the device, as sent. Google's device list may use other case.
    cloud_device_id: str | None
    name: JsonValue
    # Whether its list of features leaves out Google Assistant
    lacks_assistant: bool

    @classmethod
    def from_json(cls, info: JsonObject) -> Self:
        """Return the device's info from its reply.

        Google and Nest speakers and displays report assistant_supported as true. A
        Chromecast Audio leaves it out of its list of features, so a missing key
        counts as lacking it, as does false. No list, an empty one, or any other
        value, such as 0, null or "false", says nothing either way.
        """
        device_info = info.get("device_info")
        cloud_device_id, capabilities = (
            (device_info.get("cloud_device_id"), device_info.get("capabilities"))
            if isinstance(device_info, dict)
            else (None, None)
        )
        return cls(
            cloud_device_id=(
                cloud_device_id
                if isinstance(cloud_device_id, str) and cloud_device_id
                else None
            ),
            name=info.get("name"),
            lacks_assistant=(
                isinstance(capabilities, dict)
                and bool(capabilities)
                and capabilities.get("assistant_supported", False) is False
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildInfo:
    """A device's firmware versions and release channel."""

    # The BUILD_INFO_FIELDS that it sent, or None if it sent no build info object
    fields: JsonObject | None

    @classmethod
    def from_json(cls, info: JsonObject) -> Self:
        """Return the build info from the reply."""
        build_info = info.get("build_info")
        return cls(
            fields=(
                {
                    field: build_info[field]
                    for field in BUILD_INFO_FIELDS
                    if field in build_info
                }
                if isinstance(build_info, dict)
                else None
            )
        )

    @property
    def firmware_version(self) -> str | None:
        """Return the firmware version in the form Google publishes for the device.

        A display reports its system version, such as 31.20260101.103.1234567, as its
        system build number. A speaker or Chromecast Audio reports only a build there,
        the last part of its Cast version, such as 3.77.512345, which is the one
        Google publishes for it.
        """
        fields = self.fields or {}
        system = fields.get("system_build_number")
        cast = fields.get("cast_build_revision")
        if isinstance(system, str) and "." in system:
            return system
        if isinstance(cast, str) and cast:
            return cast
        return system if isinstance(system, str) and system else None


@dataclass(frozen=True, slots=True, kw_only=True)
class UpdateInfo:
    """Whether a device has a firmware update waiting, how long it has been up, and its Wi-Fi."""

    # In seconds; a finite number that isn't negative
    uptime: int | float | None
    # Only the JSON true or false
    has_update: bool | None
    ssid: str | None
    # In dBm; a JSON number, but not true or false. A Nest Hub sends none.
    signal_level: int | float | None

    @classmethod
    def from_json(cls, info: JsonObject) -> Self:
        """Return the update info; a field the reply leaves out or sends otherwise is None."""
        uptime = _number(info.get("uptime"))
        has_update, ssid = info.get("has_update"), info.get("ssid")
        signal = info.get("signal_level")
        return cls(
            uptime=uptime if uptime is not None and uptime >= 0 else None,
            has_update=has_update if isinstance(has_update, bool) else None,
            ssid=ssid if isinstance(ssid, str) and ssid else None,
            signal_level=(
                signal
                if isinstance(signal, int | float) and not isinstance(signal, bool)
                else None
            ),
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class BluetoothSighting:
    """A Bluetooth device that a scan heard."""

    mac_address: str
    rssi: int

    @classmethod
    def from_json(cls, row: JsonValue) -> Self | None:
        """Return the sighting, or None for a row without a string address and an integer RSSI.

        JSON true passes as an RSSI, as it does in ha-google-home.
        """
        if not isinstance(row, dict):
            return None
        mac_address, rssi = row.get("mac_address"), row.get("rssi")
        if not isinstance(mac_address, str) or not isinstance(rssi, int):
            return None
        return cls(mac_address=mac_address, rssi=rssi)
