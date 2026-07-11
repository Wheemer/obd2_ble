"""Enhanced manufacturer-specific OBD commands."""

from obdii import Command, commands as veh_commands
from obdii.parsers.formula import Formula


def _command(
    name: str,
    mode: int,
    pid: str,
    unit: str,
    formula: str,
    *,
    obd_header: str | None = None,
) -> Command:
    """Create a named command without patching py-obdii's registry."""
    command = Command(mode, pid, 2, None, None, unit, Formula(formula))
    command.name = name
    command.obd_header = obd_header
    return command


HONDA_ATF_TEMP_8220 = _command(
    "HONDA_ATF_TEMP_8220",
    0x22,
    "8220",
    "°C",
    "B-40",
)
HONDA_ATF_TEMP_9023 = _command(
    "HONDA_ATF_TEMP_9023",
    0x22,
    "9023",
    "°C",
    "B-40",
)
HONDA_ATF_TEMP_2201 = _command(
    "HONDA_ATF_TEMP_2201",
    0x22,
    "2201",
    "°C",
    "B-40",
)
HONDA_ATF_TEMP_2201_TCM_7E1 = _command(
    "HONDA_ATF_TEMP_2201_TCM_7E1",
    0x22,
    "2201",
    "°C",
    "B-40",
    obd_header="7E1",
)

ENHANCED_COMMANDS: dict[str, Command] = {
    HONDA_ATF_TEMP_8220.name: HONDA_ATF_TEMP_8220,
    HONDA_ATF_TEMP_9023.name: HONDA_ATF_TEMP_9023,
    HONDA_ATF_TEMP_2201.name: HONDA_ATF_TEMP_2201,
    HONDA_ATF_TEMP_2201_TCM_7E1.name: HONDA_ATF_TEMP_2201_TCM_7E1,
}

ENHANCED_COMMAND_LABELS: dict[str, str] = {
    HONDA_ATF_TEMP_8220.name: "Honda ATF temperature candidate 8220",
    HONDA_ATF_TEMP_9023.name: "Honda ATF temperature candidate 9023",
    HONDA_ATF_TEMP_2201.name: "Honda ATF Temp 2201",
    HONDA_ATF_TEMP_2201_TCM_7E1.name: "Honda ATF Temp 2201 TCM",
}

COMMAND_LABELS: dict[str, str] = {
    "ENGINE_SPEED": "Engine RPM",
    "VEHICLE_SPEED": "Speed",
    "ENGINE_LOAD": "Engine Load",
    "THROTTLE_POSITION": "Throttle",
    "INTAKE_AIR_TEMP": "Intake Temp",
    "FUEL_LEVEL": "Fuel Level",
    "VEHICLE_VOLTAGE": "Battery Voltage",
    "ENGINE_RUN_TIME": "Run Time",
    **ENHANCED_COMMAND_LABELS,
}


def get_command(name: str) -> Command:
    """Return a built-in or enhanced OBD command by name."""
    try:
        return veh_commands[name]
    except KeyError:
        return ENHANCED_COMMANDS[name]


def available_enhanced_commands() -> list[Command]:
    """Return opt-in enhanced commands."""
    return list(ENHANCED_COMMANDS.values())


def command_label(command: Command) -> str:
    """Return a user-facing label for a command."""
    return COMMAND_LABELS.get(
        command.name,
        " ".join(command.name.replace("_", " ").split()).capitalize(),
    )
