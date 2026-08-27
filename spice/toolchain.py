r"""Where Faradaem keeps the tools it installed for itself.

Faradaem needs two things it cannot be: a simulator and a PDK. Historically
both were the reader's problem -- download ngspice from SourceForge, unpack
it somewhere, put it on PATH; pip install ciel, fetch two gigabytes of
foundry data, set PDK_ROOT. That is an afternoon on every new machine, and
every step of it is a step that can be done slightly wrong.

So Faradaem installs them itself, with `python install.py`, into one place
this module names. Nothing here downloads anything; this is only the
agreement about location, shared by the installer that writes and by the
resolvers in runner.py that read. One definition, so the two can never
disagree about where the tool went.

Not the project folder: it lives in OneDrive, and neither a 57 MB simulator
nor a PDK belongs in a folder that syncs on write. The home is ~/.faradaem,
the same root the ledger already uses, overridable with FARADAEM_HOME.

A managed install is never required. It sits in the resolution order below
an explicit environment variable and above whatever is on PATH, so a reader
who has their own ngspice keeps it by setting FARADAEM_NGSPICE, and a reader
who has nothing gets a working tool by running the installer.
"""

import os

#: Overrides the root that holds everything Faradaem keeps between runs.
HOME_ENV_VAR = "FARADAEM_HOME"

#: Installed tools go under home()/tools, one directory per tool, so the
#: ledger and the tools cannot collide as either grows.
TOOLS_DIR = "tools"
NGSPICE_DIR = "ngspice"
PDK_DIR = "pdk"

#: Where ngspice-47's own Windows archive puts the console binary, relative
#: to the directory it is unpacked into. The archive's shape, not ours.
NGSPICE_PARTS_WINDOWS = ("Spice64", "bin", "ngspice_con.exe")

#: The conventional layout of a POSIX build. The installer never writes one
#: -- there is no official prebuilt binary to fetch -- but a reader who
#: unpacks their own build here is found without any further configuration.
NGSPICE_PARTS_POSIX = ("bin", "ngspice")

#: Written into an installed PDK, naming the upstream release it came from.
#: A full install carries its version in ciel's own directory layout; an
#: install.py one does not, and a run whose PDK version is unknown is worth
#: less as evidence, so the installer records it where the ledger can read
#: it. One line, the release identifier, nothing else.
VERSION_FILE = "faradaem-version"


def home():
    """The root Faradaem keeps things in: ~/.faradaem, or $FARADAEM_HOME.

    Resolved on every call rather than at import, so a shell that gains the
    variable does not need the server restarted to be believed.
    """
    chosen = os.environ.get(HOME_ENV_VAR, "").strip()
    if chosen:
        return chosen
    return os.path.join(os.path.expanduser("~"), ".faradaem")


def tools_root():
    """The directory the installer unpacks into."""
    return os.path.join(home(), TOOLS_DIR)


def ngspice_root():
    """Where an installed ngspice is unpacked, present or not."""
    return os.path.join(tools_root(), NGSPICE_DIR)


def ngspice_exe():
    """The path an installed ngspice binary would have on this platform."""
    parts = NGSPICE_PARTS_WINDOWS if os.name == "nt" else NGSPICE_PARTS_POSIX
    return os.path.join(ngspice_root(), *parts)


def managed_ngspice():
    """The installed ngspice binary, or None when there is not one.

    Existence only. Whether it runs is the caller's question, and doctor.py
    answers it by running the thing rather than by trusting this.
    """
    path = ngspice_exe()
    return path if os.path.isfile(path) else None


def pdk_root():
    """Where an installed PDK is unpacked, present or not.

    This is a PDK *root* in the usual sense: the directory that contains
    sky130A, so it can be handed to $PDK_ROOT unchanged.
    """
    return os.path.join(tools_root(), PDK_DIR)


def managed_pdk(lib_parts):
    """The installed PDK root, or None when the model library is not there.

    Takes the library's path parts rather than importing runner, because
    runner imports this module and a cycle would be worse than a parameter.
    """
    root = pdk_root()
    return root if os.path.isfile(os.path.join(root, *lib_parts)) else None


def recorded_version(root):
    """The upstream release an installed tree came from, or None.

    None is the honest answer for a tree this installer did not write; the
    caller says "unknown" rather than guessing from the directory name.
    """
    path = os.path.join(root, VERSION_FILE)
    try:
        with open(path, "r", encoding="ascii") as stream:
            return stream.read().strip() or None
    except (OSError, ValueError):
        return None
