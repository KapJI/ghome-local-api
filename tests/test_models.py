"""The rules that read each reply, as pure tables, and the device URL."""

import math

import pytest

from ghome_local_api import (
    Alarm,
    AlarmsAndTimers,
    AlarmStatus,
    AlarmVolumeReply,
    BluetoothSighting,
    BuildInfo,
    DeleteResult,
    DeviceInfo,
    DoNotDisturbReply,
    JsonValue,
    Timer,
    TimerStatus,
    UpdateInfo,
    device_url,
)

ALARM: dict[str, JsonValue] = {
    "id": "alarm/a1",
    "fire_time": 1700000000000,
    "status": 1,
    "label": "Wake up",
    "recurrence": [1, 2],
}
TIMER: dict[str, JsonValue] = {
    "id": "timer/t1",
    "original_duration": 600000,
    "fire_time": 1700000600000,
    "remaining_duration": 300000,
    "status": 2,
    "label": None,
}


@pytest.mark.parametrize(
    ("host", "port", "url"),
    [
        ("192.168.1.20", 8443, "https://192.168.1.20:8443/setup/reboot"),
        ("fd00::20", 8443, "https://[fd00::20]:8443/setup/reboot"),
        ("10.0.0.5", 9000, "https://10.0.0.5:9000/setup/reboot"),
    ],
)
def test_device_url(host: str, port: int, url: str) -> None:
    """An IPv6 address is bracketed, and the port is the one given."""
    assert device_url(host, "setup/reboot", port) == url


@pytest.mark.parametrize("host", ["speaker.local", "[fd00::20]", ""])
def test_device_url_needs_an_address(host: str) -> None:
    """A host that isn't an IP address is refused."""
    with pytest.raises(
        ValueError, match="does not appear to be an IPv4 or IPv6 address"
    ):
        device_url(host, "setup/reboot")


def test_alarms_and_timers() -> None:
    """A valid reply keeps its items' values as sent, and the reply itself."""
    body: dict[str, JsonValue] = {
        "alarm": [ALARM, {"id": "alarm/a2", "fire_time": 1.5, "status": 4}],
        "timer": [TIMER, {"id": "timer/t2", "original_duration": 1000, "status": 1}],
    }
    reply = AlarmsAndTimers.from_json(body)
    assert reply == AlarmsAndTimers(
        alarms=(
            Alarm(
                id="alarm/a1",
                fire_time=1700000000000,
                status=AlarmStatus.SET,
                label="Wake up",
                recurrence=[1, 2],
            ),
            Alarm(
                id="alarm/a2",
                fire_time=1.5,
                status=AlarmStatus.INACTIVE,
                label=None,
                recurrence=None,
            ),
        ),
        timers=(
            Timer(
                id="timer/t1",
                original_duration=600000,
                fire_time=1700000600000,
                remaining_duration=300000,
                status=TimerStatus.PAUSED,
                label=None,
            ),
            Timer(
                id="timer/t2",
                original_duration=1000,
                fire_time=None,
                remaining_duration=None,
                status=TimerStatus.SET,
                label=None,
            ),
        ),
        raw=body,
    )
    assert reply is not None
    assert reply.raw is body


@pytest.mark.parametrize(
    ("alarm", "readable"),
    [
        ({"status": True}, False),
        ({"status": 1.0}, False),
        ({"status": None}, False),
        ({"id": 5}, False),
        ({"id": None}, False),
        ({"fire_time": True}, False),
        ({"fire_time": "1700000000000"}, False),
        ({"fire_time": math.nan}, False),
        ({"fire_time": math.inf}, False),
        ({"fire_time": None}, False),
        # A whole number too large for a float is still a number here
        ({"fire_time": 10**400}, True),
        ({"label": 5, "recurrence": {"days": [1]}}, True),
    ],
)
def test_alarm_boundaries(alarm: dict[str, JsonValue], readable: bool) -> None:
    """An alarm needs a string ID, a whole-number status and a number fire time."""
    assert (Alarm.from_json(ALARM | alarm) is not None) == readable


@pytest.mark.parametrize(
    ("timer", "readable"),
    [
        ({"original_duration": None}, False),
        ({"original_duration": False}, False),
        ({"fire_time": None, "remaining_duration": None}, True),
        ({"fire_time": "soon"}, False),
        ({"remaining_duration": math.nan}, False),
        ({"remaining_duration": True}, False),
        ({"status": "2"}, False),
    ],
)
def test_timer_boundaries(timer: dict[str, JsonValue], readable: bool) -> None:
    """A timer needs a number duration; its fire time and time left may be null, but not text."""
    assert (Timer.from_json(TIMER | timer) is not None) == readable


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {"alarm": [ALARM]},
        {"alarm": {}, "timer": []},
        {"alarm": [], "timer": None},
        # One unreadable item rejects the whole reply, so old and new items never mix
        {"alarm": [ALARM, "alarm/a2"], "timer": [TIMER]},
        {"alarm": [ALARM], "timer": [TIMER, TIMER | {"status": None}]},
        {"alarm": [], "timer": [TIMER, ["timer/t2"]]},
    ],
)
def test_alarms_and_timers_in_another_form(body: JsonValue) -> None:
    """A reply without a list of each, or with any unreadable item, is unreadable."""
    assert AlarmsAndTimers.from_json(body) is None


@pytest.mark.parametrize(
    ("value", "alarm", "timer"),
    [
        (0, AlarmStatus.NONE, TimerStatus.NONE),
        (2, AlarmStatus.RINGING, TimerStatus.PAUSED),
        (3, AlarmStatus.SNOOZED, TimerStatus.RINGING),
        (5, AlarmStatus.MISSED, TimerStatus.UNKNOWN),
        (6, AlarmStatus.UNKNOWN, TimerStatus.UNKNOWN),
        (-7, AlarmStatus.UNKNOWN, TimerStatus.UNKNOWN),
    ],
)
def test_statuses(value: int, alarm: AlarmStatus, timer: TimerStatus) -> None:
    """Known statuses keep their names; any other whole number is UNKNOWN."""
    parsed_alarm = Alarm.from_json(ALARM | {"status": value})
    parsed_timer = Timer.from_json(TIMER | {"status": value})
    assert parsed_alarm is not None
    assert parsed_timer is not None
    assert (parsed_alarm.status, parsed_timer.status) == (alarm, timer)


@pytest.mark.parametrize(
    ("raw", "confirmed"),
    [
        ({"success": True}, True),
        ({"success": True, "extra": 1}, True),
        ({"success": 1}, False),
        ({"success": "true"}, False),
        ({"success": False}, False),
        ({}, False),
        ([{"success": True}], False),
        (None, False),
    ],
)
def test_delete_confirmation(raw: JsonValue, confirmed: bool) -> None:
    """Only an object with success exactly true confirms a delete."""
    assert DeleteResult(raw=raw).confirmed is confirmed


@pytest.mark.parametrize(
    ("raw", "has_volume", "percent"),
    [
        ({"volume": 0.7}, True, 70),
        ({"volume": "0.7"}, True, 70),
        ({"volume": 5}, True, 500),
        ({"volume": 0.004}, True, 0),
        ({"volume": "loud"}, True, ValueError),
        # str(True) is "True", which isn't a number
        ({"volume": True}, True, ValueError),
        ({"volume": None}, True, ValueError),
        ({"level": 0.7}, False, ValueError),
        ({}, False, ValueError),
        # A list that contains the key isn't an object with it
        (["volume"], False, ValueError),
        ("volume", False, ValueError),
        (None, False, ValueError),
    ],
)
def test_alarm_volume(
    raw: JsonValue, has_volume: bool, percent: int | type[Exception]
) -> None:
    """The volume is a fraction, as a number or a numeric string; anything else raises."""
    volume = AlarmVolumeReply(raw=raw)
    assert volume.has_volume is has_volume
    if isinstance(percent, int):
        assert volume.percent() == percent
    else:
        with pytest.raises(percent):
            volume.percent()


@pytest.mark.parametrize(
    ("raw", "enabled"),
    [
        ({"notifications_enabled": False}, True),
        ({"notifications_enabled": True}, False),
        # Python truthiness, as ha-google-home reads it
        ({"notifications_enabled": 0}, True),
        ({"notifications_enabled": ""}, True),
        ({"notifications_enabled": "false"}, False),
        ({"notifications_enabled": None}, True),
        ({}, None),
        (["notifications_enabled"], None),
        (None, None),
    ],
)
def test_do_not_disturb(raw: JsonValue, enabled: bool | None) -> None:
    """Do Not Disturb is the opposite of the notifications setting, if the reply has it."""
    assert DoNotDisturbReply(raw=raw).enabled is enabled


@pytest.mark.parametrize(
    ("device_info", "lacks_assistant"),
    [
        ({"capabilities": {"assistant_supported": False}}, True),
        # A Chromecast Audio leaves the key out
        ({"capabilities": {"audio_supported": True}}, True),
        ({"capabilities": {"assistant_supported": True}}, False),
        ({"capabilities": {"assistant_supported": 0}}, False),
        ({"capabilities": {"assistant_supported": None}}, False),
        ({"capabilities": {"assistant_supported": "false"}}, False),
        ({"capabilities": {}}, False),
        ({"capabilities": ["assistant_supported"]}, False),
        ({}, False),
        ("capabilities", False),
        (None, False),
    ],
)
def test_lacks_assistant(device_info: JsonValue, lacks_assistant: bool) -> None:
    """Only a non-empty list of features without assistant_supported, or with it false, lacks it."""
    info = DeviceInfo.from_json({"device_info": device_info})
    assert info.lacks_assistant is lacks_assistant


@pytest.mark.parametrize(
    ("info", "cloud_device_id"),
    [
        ({"device_info": {"cloud_device_id": "ABCdef01"}}, "ABCdef01"),
        ({"device_info": {"cloud_device_id": ""}}, None),
        ({"device_info": {"cloud_device_id": 5}}, None),
        ({"device_info": {}}, None),
        ({"cloud_device_id": "ABCdef01"}, None),
    ],
)
def test_cloud_device_id(
    info: dict[str, JsonValue], cloud_device_id: str | None
) -> None:
    """The ID is a non-empty string from device_info, in the case it was sent."""
    assert DeviceInfo.from_json(info).cloud_device_id == cloud_device_id


def test_device_name() -> None:
    """The name is kept as sent, whatever JSON it is."""
    assert DeviceInfo.from_json({"name": "Kitchen"}).name == "Kitchen"
    assert DeviceInfo.from_json({"name": ["Kitchen"]}).name == ["Kitchen"]
    assert DeviceInfo.from_json({}).name is None


def test_build_info_fields() -> None:
    """Only the version and release channel fields are kept, as sent."""
    info: dict[str, JsonValue] = {
        "build_info": {
            "cast_build_revision": "3.77.512345",
            "release_track": "stable-channel",
            "build_type": 2,
            "system_build_number": 512345,
        },
        "name": "Kitchen",
    }
    assert BuildInfo.from_json(info).fields == {
        "cast_build_revision": "3.77.512345",
        "release_track": "stable-channel",
        "system_build_number": 512345,
    }
    assert BuildInfo.from_json({"build_info": "3.77"}).fields is None
    assert BuildInfo.from_json({}).fields is None


@pytest.mark.parametrize(
    ("fields", "version"),
    [
        # A display's system version
        (
            {
                "system_build_number": "31.20260101.103.1234567",
                "cast_build_revision": "3.77.512345",
            },
            "31.20260101.103.1234567",
        ),
        # A speaker's build number is only part of its Cast version
        (
            {"system_build_number": "512345", "cast_build_revision": "3.77.512345"},
            "3.77.512345",
        ),
        ({"system_build_number": "512345", "cast_build_revision": ""}, "512345"),
        ({"system_build_number": "512345"}, "512345"),
        ({"system_build_number": 31.2, "cast_build_revision": 3}, None),
        ({"system_build_number": ""}, None),
        ({}, None),
        (None, None),
    ],
)
def test_firmware_version(
    fields: dict[str, JsonValue] | None, version: str | None
) -> None:
    """The version Google publishes: a display's system version, else the Cast version."""
    assert BuildInfo(fields=fields).firmware_version == version


@pytest.mark.parametrize(
    ("info", "expected"),
    [
        (
            {"uptime": 3600.5, "has_update": True, "ssid": "Home", "signal_level": -60},
            UpdateInfo(uptime=3600.5, has_update=True, ssid="Home", signal_level=-60),
        ),
        (
            {"uptime": 0, "has_update": False, "ssid": "", "signal_level": -60.5},
            UpdateInfo(uptime=0, has_update=False, ssid=None, signal_level=-60.5),
        ),
        (
            {"uptime": -1, "has_update": 1, "ssid": 5, "signal_level": True},
            UpdateInfo(uptime=None, has_update=None, ssid=None, signal_level=None),
        ),
        (
            {"uptime": True, "has_update": "true", "signal_level": "-60"},
            UpdateInfo(uptime=None, has_update=None, ssid=None, signal_level=None),
        ),
        (
            {"uptime": math.inf},
            UpdateInfo(uptime=None, has_update=None, ssid=None, signal_level=None),
        ),
        (
            {"uptime": "3600"},
            UpdateInfo(uptime=None, has_update=None, ssid=None, signal_level=None),
        ),
    ],
)
def test_update_info(info: dict[str, JsonValue], expected: UpdateInfo) -> None:
    """Each field is kept only in the form expected, else it is None."""
    assert UpdateInfo.from_json(info) == expected


@pytest.mark.parametrize(
    ("row", "sighting"),
    [
        (
            {"mac_address": "00:11:22:33:44:55", "rssi": -60, "name": "Phone"},
            BluetoothSighting(mac_address="00:11:22:33:44:55", rssi=-60),
        ),
        # JSON true passes, as in ha-google-home (its P2 proposal would reject it)
        (
            {"mac_address": "00:11:22:33:44:55", "rssi": True},
            BluetoothSighting(mac_address="00:11:22:33:44:55", rssi=True),
        ),
        ({"mac_address": "00:11:22:33:44:55", "rssi": -60.0}, None),
        ({"mac_address": "00:11:22:33:44:55", "rssi": "-60"}, None),
        ({"mac_address": "00:11:22:33:44:55"}, None),
        ({"mac_address": 1, "rssi": -60}, None),
        (["00:11:22:33:44:55", -60], None),
        (None, None),
    ],
)
def test_bluetooth_row(row: JsonValue, sighting: BluetoothSighting | None) -> None:
    """A row needs a string address and an integer RSSI."""
    # The repr tells true from 1, which compare equal
    assert repr(BluetoothSighting.from_json(row)) == repr(sighting)
