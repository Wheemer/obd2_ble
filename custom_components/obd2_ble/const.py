"""Constants for OBD2 BLE."""

# Base component constants
from datetime import timedelta

from homeassistant.const import Platform

NAME = "OBD2 BLE"
DOMAIN = "obd2_ble"
DOMAIN_DATA = f"{DOMAIN}_data"

ATTRIBUTION = "Data provided by http://jsonplaceholder.typicode.com/"
ISSUE_URL = "https://github.com/dala318/obd2_ble/issues"

# Platforms
PLATFORMS: list[Platform] = [Platform.SENSOR]

# Configuration and options
CONF_AUTO_CONFIGURE = "auto_configure"
CONF_CACHED_VALUES = "cached_values"
CONF_COMMANDS = "commands"
CONF_FAST_POLL = "fast_poll"
CONF_SLOW_POLL = "slow_poll"
CONF_XS_POLL = "xs_poll"
CONF_CHARACTERISTIC_UUID_READ = "characteristic_uuid_read"
CONF_CHARACTERISTIC_UUID_WRITE = "characteristic_uuid_write"
CONF_ENABLED = "enabled"
CONF_PROTOCOL = "protocol"
CONF_SERVICE_UUID = "service_uuid"

# Defaults
DEFAULT_NAME = DOMAIN
DEFAULT_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
DEFAULT_CHARACTERISTIC_UUID_READ = "0000fff1-0000-1000-8000-00805f9b34fb"
DEFAULT_CHARACTERISTIC_UUID_WRITE = "0000fff1-0000-1000-8000-00805f9b34fb"
DEFAULT_CACHED_VALUES = False
DEFAULT_FAST_POLL = 10
DEFAULT_SLOW_POLL = 300
DEFAULT_XS_POLL = 3600

# when the device is in range, and the car is on, poll quickly to get
# as much data as we can before it turns off
FAST_POLL_INTERVAL = timedelta(seconds=10)

# when the device is in range, but the car is off, we need to poll
# occasionally to see whether the car has be turned back on. On some cars
# this causes a relay to click every time, so this interval needs to be
# as long as possible to prevent excessive wear on the relay.
SLOW_POLL_INTERVAL = timedelta(minutes=5)

# when the device is out of range, use ultra slow polling since a bluetooth
# advertisement message will kick it back into life when back in range.
# see __init__.py: _async_specific_device_found()
ULTRA_SLOW_POLL_INTERVAL = timedelta(hours=1)


STARTUP_MESSAGE = f"""
-------------------------------------------------------------------
{NAME}
This is a custom integration!
If you have any issues with this you need to open an issue here:
{ISSUE_URL}
-------------------------------------------------------------------
"""
