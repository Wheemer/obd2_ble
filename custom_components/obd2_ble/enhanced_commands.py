"""Enhanced manufacturer-specific OBD commands."""

from obdii.command import Command
from obdii.modes import Modes
from obdii.modes.group_commands import GroupCommands
from obdii.parsers.formula import Formula

M = 22

class Mode22(GroupCommands, registry_id=M):
    """Enhanced manufacturer-specific OBD commands."""

    HONDA_ATF_TEMP_8220 = Command(
        mode=M,
        pid=8220,
        units="°C",
        resolver=Formula("A-40")
    )
    """Honda ATF temperature candidate 8220"""

    HONDA_ATF_TEMP_9023 = Command(
        mode=M,
        pid=9023,
        units="°C",
        resolver=Formula("A-40")
    )
    """Honda ATF temperature candidate 9023"""


class ExtendedCommands(Mode22, Modes):
    """Extended commands GroupModes."""
ext_commands = ExtendedCommands()


def command_label(command: Command) -> str:
    """Return a user-facing label for a command."""
    if command.mode == M and command.__doc__:
        return command.__doc__
    return command.name.replace('_', ' ').capitalize()




