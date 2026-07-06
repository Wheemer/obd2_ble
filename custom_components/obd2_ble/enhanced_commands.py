"""Enhanced manufacturer-specific OBD commands."""

from obdii import Command, commands as veh_commands
from obdii.parsers.formula import Formula


def _command(name: str, mode: int, pid: str, unit: str, formula: str) -> Command:
    """Create a named command without patching py-obdii's registry."""
    command = Command(mode, pid, 2, None, None, unit, Formula(formula))
    command.name = name
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

ENHANCED_COMMANDS: dict[str, Command] = {
    HONDA_ATF_TEMP_8220.name: HONDA_ATF_TEMP_8220,
    HONDA_ATF_TEMP_9023.name: HONDA_ATF_TEMP_9023,
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
