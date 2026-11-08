# ghome-local-api

[![CI](https://github.com/KapJI/ghome-local-api/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/KapJI/ghome-local-api/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/ghome-local-api)](https://pypi.org/project/ghome-local-api/)
[![Coverage](https://codecov.io/gh/KapJI/ghome-local-api/graph/badge.svg)](https://app.codecov.io/gh/KapJI/ghome-local-api)

An asynchronous Python client for the local API of Google Home devices, moved out of [ha-google-home](https://github.com/leikoilja/ha-google-home).

It reads and deletes alarms and timers, reads and sets the alarm volume and Do Not Disturb, reboots a device, asks it about itself and its firmware, and runs its Bluetooth scans. It is a thin protocol client: it sends one request per call over your [aiohttp](https://docs.aiohttp.org/) session and returns typed replies. It keeps no state, retries nothing and doesn't log.

## Status

Versions before 1.0.0 may still change the API.

These are unofficial endpoints: Google doesn't document them and can change them in a firmware update. Not every device has every endpoint. One that doesn't usually answers 404, which raises `NotFoundError`, but some refuse the connection instead, which raises `CannotConnectError`. A device that is starting can also answer 404 for a while.

## What you need

- Python 3.12 or newer, and aiohttp 3.14.4 or newer.
- The device's IP address. A host name raises `ValueError`.
- For most calls, the device's current local auth token. This package doesn't get tokens: [glocaltokens](https://github.com/leikoilja/glocaltokens) does. The info calls need no token.

## Install

```console
$ pip install ghome-local-api
```

## Quickstart

This prints a device's alarms and timers. It changes nothing on the device.

```python
import asyncio
from datetime import UTC, datetime

import aiohttp

from ghome_local_api import GoogleHomeLocalClient


async def main() -> None:
    # The devices use self-signed certificates
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        client = GoogleHomeLocalClient(session)
        reply = await client.get_alarms_and_timers(
            "192.168.1.20", "YOUR_LOCAL_AUTH_TOKEN"
        )
        for alarm in reply.alarms:
            # Times are in milliseconds since the epoch
            fires = datetime.fromtimestamp(alarm.fire_time / 1000, UTC)
            print(alarm.id, alarm.status.name, fires)
        for timer in reply.timers:
            print(timer.id, timer.status.name, timer.remaining_duration)


asyncio.run(main())
```

You open and close the session; the client never closes it. `ssl=False` accepts the device's self-signed certificate but also turns off certificate checks, so a device on your network can't be told from an impostor that receives the token. Use it only on a network you trust.

## Calls

Each call takes the device's IP address first, then its token if it needs one. Every call also takes `timeout` (seconds, default 5) and `on_response` (see [Seeing the answer early](#seeing-the-answer-early)).

| Call                                                                          | Returns                                                                                                                                                                                |
| ----------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `get_alarms_and_timers(host, token)`                                          | `AlarmsAndTimers`: `alarms` and `timers`, with times in milliseconds as the device sent them                                                                                           |
| `delete_alarms_and_timers(host, token, ids)`                                  | `DeleteResult`: `confirmed` is true only if the device replied `{"success": true}`                                                                                                     |
| `get_alarm_volume(host, token)`, `set_alarm_volume(host, token, level)`       | `AlarmVolumeReply`: `has_volume` and `percent()`. `level` is a fraction, such as `0.7`                                                                                                 |
| `get_do_not_disturb(host, token)`, `set_do_not_disturb(host, token, enabled)` | `DoNotDisturbReply`: `enabled`, or `None` if the reply doesn't say                                                                                                                     |
| `reboot(host, token)`                                                         | `None` once the device answers 200, which it does even if it can't reboot                                                                                                              |
| `start_bluetooth_scan(host, token, duration=...)`                             | `None`. The device scans for up to `duration` seconds, clearing the last scan's results                                                                                                |
| `get_bluetooth_scan_results(host, token)`                                     | a tuple of `BluetoothSighting(mac_address, rssi)`. Rows without a string address and an integer RSSI are left out, so a reply of only such rows looks like a scan that heard nothing   |
| `get_device_info(host)`                                                       | `DeviceInfo`: `cloud_device_id`, `name` and `lacks_assistant`, or `None` if the reply isn't a JSON object                                                                              |
| `get_build_info(host)`                                                        | `BuildInfo`: `fields` and `firmware_version`, or `None` as above                                                                                                                       |
| `get_update_info(host)`                                                       | `UpdateInfo`: `uptime`, `has_update`, `ssid` and `signal_level`, or `None` as above                                                                                                    |
| `get_info_status(host)`                                                       | the HTTP status of the info request, without following redirects. A 200 counts only once its whole body has come; if the body breaks off, it raises a `RequestError` with `status` 200 |

An alarm or timer status that this package doesn't know is `UNKNOWN`. An alarms reply without a list of alarms and a list of timers, or with any item that can't be read, raises `UnexpectedResponseError`, so you never get only part of the reply.

The settings replies accept any JSON. Their `raw` attribute holds the decoded reply, and their helpers never raise `TypeError` for its shape. `percent()` raises `ValueError` if there is no volume or it isn't a number, and `OverflowError` if it is infinite.

## Errors

All errors derive from `GoogleHomeLocalError` and have `host` and `path` attributes.

| Error                     | When                                                                                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `CannotConnectError`      | the connection failed, for example because the device is offline                                                                                                      |
| `ConnectionLostError`     | the connection closed or the answer broke off                                                                                                                         |
| `AnswerTimeoutError`      | the device didn't answer within `timeout`                                                                                                                             |
| `ClientRequestError`      | aiohttp failed the request otherwise, for example after too many redirects                                                                                            |
| `TokenRejectedError`      | the device answered 401: get a new token                                                                                                                              |
| `NotFoundError`           | the device answered 404                                                                                                                                               |
| `StatusError`             | any other status than 200. It has `status`, and `reason`, the text the device sent with the status, which its message leaves out. The two errors above derive from it |
| `InvalidJsonError`        | a 200 whose JSON can't be decoded. The info calls return `None` instead                                                                                               |
| `UnexpectedResponseError` | an alarms or Bluetooth results reply in another form. It has the reply as `body`                                                                                      |

The first four derive from `RequestError`. Its `__cause__` is aiohttp's exception or `TimeoutError`, and its `status` is the status of an answer that came before the failure, such as a 200 whose body broke off, or `None`.

Other exceptions pass through unchanged, including `asyncio.CancelledError`, `ValueError` for a host that isn't an IP address, and what `on_response` raises, unless that is an aiohttp `ClientError` or `TimeoutError`, which is classified like a failure of the request.

## Timeouts, cancellation and repeated requests

- Each call's `timeout` replaces the session's timeout for that request.
- Cancelling a call raises `CancelledError` once its response is released. If you wrap a call in your own `asyncio.timeout`, you get `TimeoutError`, not a package error.
- An action that is cancelled, times out or loses its connection after it was sent may still have been carried out. Check the device's state before you send it again.
- The package retries nothing. After a lost connection, aiohttp may send a GET again once (`get_alarms_and_timers`, `get_bluetooth_scan_results` and the info calls), but never a POST.
- Redirects are followed, except by `get_info_status`. A 307 or 308 answer to an action makes aiohttp send the same POST to the new location; a 301, 302 or 303 makes it send a GET there.

## Session requirements

The client calls `session.request(...)` or `session.get(...)` with only the URL, headers, JSON body, timeout and, for `get_info_status`, `allow_redirects=False`. Your session needs:

- a connector that accepts the device's self-signed certificate;
- `raise_for_status` unset;
- no middleware that sends requests again.

The client reads JSON with `response.json()`, so your session's response class decides how JSON is decoded. With aiohttp's default class, Python's `json` module decodes `NaN`, `Infinity` and `1e999`, so `percent()` raises for them, and a huge integer alarm status is `UNKNOWN`. Home Assistant's session decodes with orjson, which rejects the first three (`InvalidJsonError`) and reads a huge integer as a float, which makes the alarms reply unreadable. Python's `json` module also raises `ValueError` for an integer of more than 4300 digits (Python's default limit) and `RecursionError` for very deeply nested JSON. Before parsing, aiohttp's `ClientResponse.json()` decodes the body as text with the charset from the response's `Content-Type`, so a charset that names a Python codec that isn't a text encoding, such as `charset=base64`, raises `LookupError`. The client treats any `ValueError`, `RecursionError` or `LookupError` from decoding as JSON that can't be decoded, which also covers `json.JSONDecodeError`, `UnicodeDecodeError` and orjson's errors. Decoders that raise something else for a body they can't decode aren't supported.

## Seeing the answer early

`on_response` is called once per call with aiohttp's response, as soon as the status and headers of the final response have come and before the body is read. It isn't called if no response comes. Use it to act on the status as early as possible. For example, ha-google-home notes at a 200 that the device accepted its token, so cancelling the call while the body is read doesn't undo that. It must not read, release or close the response. What it raises leaves the call, and an aiohttp `ClientError` or `TimeoutError` is classified like a failure of the request.

## Sensitive data

The client holds no token: you pass it to each call. Error messages name the host, path and status, but not the token, the reply or the device's reason text. Some objects do carry sensitive data:

- the aiohttp exception in `__cause__` can hold the request, including the token header;
- a redirect that aiohttp follows sends the token header to the new location, even on another host: aiohttp removes only standard credential headers, such as `Authorization`, when the host changes;
- `raw`, `UnexpectedResponseError.body`, `StatusError.reason` and the response passed to `on_response` hold what the device sent, such as alarm labels and device IDs.

Don't log or store these objects whole.

## Origin

The client was moved from ha-google-home at commit [`0e3feba`](https://github.com/leikoilja/ha-google-home/tree/0e3febad86d6adf777b97b05452d5d841c3edfa8).

## License

MIT, see [LICENSE](https://github.com/KapJI/ghome-local-api/blob/main/LICENSE). Its `Copyright (c) 2020 Ilja Leiko` notice covers the client code moved from ha-google-home.

## Contributing

See [CONTRIBUTING.md](https://github.com/KapJI/ghome-local-api/blob/main/CONTRIBUTING.md).
