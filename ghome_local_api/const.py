"""The device's port, paths, header names and JSON keys, and the default timeout."""

from typing import Final

DEFAULT_PORT: Final = 8443
# How long a device has to answer a request, in seconds. A busy device can take
# more than 2 seconds.
DEFAULT_TIMEOUT: Final = 5.0

# Paths that need the device's local auth token
API_ENDPOINT_ALARMS: Final = "setup/assistant/alarms"
API_ENDPOINT_ALARM_DELETE: Final = "setup/assistant/alarms/delete"
API_ENDPOINT_ALARM_VOLUME: Final = "setup/assistant/alarms/volume"
API_ENDPOINT_REBOOT: Final = "setup/reboot"
API_ENDPOINT_DO_NOT_DISTURB: Final = "setup/assistant/notifications"
API_ENDPOINT_BLUETOOTH_SCAN: Final = "setup/bluetooth/scan"
API_ENDPOINT_BLUETOOTH_RESULTS: Final = "setup/bluetooth/scan_results"

# Paths that answer without a token. The first identifies a device and shows
# whether it has Google Assistant.
API_ENDPOINT_EUREKA_INFO: Final = "setup/eureka_info?params=name,device_info"
# The device's firmware versions. A display's system version is only here.
API_ENDPOINT_BUILD_INFO: Final = "setup/eureka_info?params=build_info"
# Without params, the info has whether the device has a firmware update waiting,
# which no params give, and its Wi-Fi network and signal strength.
API_ENDPOINT_UPDATE_INFO: Final = "setup/eureka_info"

HEADER_CAST_LOCAL_AUTH: Final = "cast-local-authorization-token"
HEADER_CONTENT_TYPE: Final = "content-type"

JSON_ALARM: Final = "alarm"
JSON_TIMER: Final = "timer"
JSON_NOTIFICATIONS_ENABLED: Final = "notifications_enabled"
JSON_ALARM_VOLUME: Final = "volume"
