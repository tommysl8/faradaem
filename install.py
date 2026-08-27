r"""faradaem install: fetch the simulator and the PDK, once, per machine.

Faradaem needs two things it is not: ngspice, which produces every number
the tool reports, and the SKY130 PDK, which says what the transistors are.
Getting both onto a new machine used to be an afternoon -- unpack a
SourceForge archive, put it on PATH, pip install ciel, pull two gigabytes of
foundry data, set PDK_ROOT, discover which of the five steps you got wrong
when the first simulation fails. This does all of it in one command.

    python install.py            install whatever is missing
    python install.py --list     say exactly what it would fetch, fetch nothing
    python install.py --force    fetch again even if something is already there

Where it goes: ~/.faradaem/tools, named by spice/toolchain.py and found by
the same resolvers the tool itself uses, so nothing needs to go on PATH and
no environment variable needs setting. Never the project folder, which
syncs.

What it fetches, and why so little: three archives, each from the project
that publishes it. The PDK half is 21 MB rather than the 2.2 GB a full
install costs, because Faradaem is an analog tool: it needs the technology
files and the primitive devices, and none of the standard cells, IO pads or
SRAM macros that the gigabytes are.

Every download is pinned by SHA-256 and refused on any mismatch, because a
binary that is about to be executed is not taken on trust. Run --list first
if you would rather read the URLs before trusting them.

Unpacking uses the tar already on the machine: Windows ships bsdtar with
libarchive, which reads .7z and .tar.zst without help. Nothing is pip
installed and the server stays standard-library-only.

Optional pieces are left alone. KLayout, and the API keys the strategist
wants, are not installed here; doctor.py reports them as absent and the
features that need them say so.
"""

import argparse
import hashlib
import html
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doctor  # noqa: E402
from spice import runner, toolchain  # noqa: E402

#: Named so a maintainer reading a server log knows who was fetching.
USER_AGENT = "faradaem-install (+https://faradaem.com)"

#: Read in chunks so a 14 MB download can report progress and so the hash
#: is computed on the way past rather than by re-reading the file.
CHUNK = 256 * 1024

#: How many times one archive is attempted before the step gives up. Mirrors
#: differ; a checksum failure on one of them is worth another try, and the
#: pin means a retry can never turn into accepting the wrong file.
ATTEMPTS = 3

#: ngspice's own Windows build, from the project's own SourceForge files.
#: This URL sometimes answers with the archive and sometimes with a landing
#: page that carries the mirror link; _open_archive handles both.
NGSPICE = {
    "what": "ngspice 47, Windows x64",
    "url": "https://sourceforge.net/projects/ngspice/files/"
           "ng-spice-rework/47/ngspice-47_64.7z/download",
    "archive": "ngspice-47_64.7z",
    "sha256": "59225971bd68cdd1199443649aa4615a9e6d684933f205ab49006a3942518f5a",
    "bytes": 13814879,
}

#: The SKY130 release this project is pinned to, named the way the PDK names
#: itself: by the commit its files were built from. Recorded in the install
#: so the ledger can stamp it on every run made against these models.
PDK_VERSION = "7b70722e33c03fcb5dabcf4d479fb0822d9251c9"

#: Where that release's archives live. Built from the version above, so the
#: URLs and the version recorded in the install cannot say different things.
PDK_BASE = ("https://github.com/fossi-foundation/ciel-releases/releases/"
            "download/sky130-" + PDK_VERSION + "/")

#: Two of that release's archives, unpacked into one tree.
#:
#: 'common' is the whole of libs.tech: the model library, the magic
#: technology file, the KLayout sign-off decks. It is not enough on its own,
#: which is not obvious and cost an afternoon to learn -- the per-corner
#: files inside it include the device models by relative path out of
#: libs.ref, so a tree with only common loads until the first transistor and
#: then stops with "could not find include file".
#:
#: 'sky130_fd_pr' is that missing half: the primitive devices, the only
#: library in the release Faradaem reads. The dozen archives beside these two
#: are standard cells, IO pads and SRAM macros, which an analog tool never
#: opens, and they are where the two gigabytes are.
PDK_ARCHIVES = (
    {
        "what": "SKY130A technology files, version " + PDK_VERSION[:8],
        "url": PDK_BASE + "common.tar.zst",
        "archive": "common.tar.zst",
        "sha256": "95ea4e7cdc9ca4fe9a58855f3cbda2994758763faba36399e8c04e0f0"
                  "3621846",
        "bytes": 6601216,
    },
    {
        "what": "SKY130 primitive device models",
        "url": PDK_BASE + "sky130_fd_pr.tar.zst",
        "archive": "sky130_fd_pr.tar.zst",
        "sha256": "a55c3de9b40b9ed58697f2f70daa3569495af5ebf07a8186daf2814fc"
                  "e184f75",
        "bytes": 14275322,
    },
)

#: The corner file whose includes reach out of libs.tech into the device
#: models. Whether these resolve is the difference between an install that
#: works and one that loads until the first transistor and then stops.
CORNER_PROBE_PARTS = ("sky130A", "libs.tech", "ngspice", "corners",
                      "tt.spice")

#: What a step did. Closed on purpose, like every other verdict set in the
#: project: a step either put something there, found it already there, or
#: could not, and "could not" is never dressed up as either of the others.
INSTALLED, PRESENT, UNAVAILABLE, FAILED = (
    "installed", "present", "unavailable", "failed")

MARKS = {INSTALLED: "ok", PRESENT: "--", UNAVAILABLE: "--", FAILED: "XX"}


class InstallError(RuntimeError):
    """Raised when a step cannot finish, carrying what to do about it."""


# ---------------------------------------------------------------------------
# the tar already on the machine
# ---------------------------------------------------------------------------


def _tar_flavour(path):
    """Return (is_bsdtar, has_zstd) for a tar binary, by asking it."""
    try:
        banner = subprocess.run([path, "--version"], capture_output=True,
                                text=True, timeout=30).stdout.lower()
    except (OSError, subprocess.SubprocessError):
        return False, False
    return "bsdtar" in banner or "libarchive" in banner, "libzstd" in banner


def find_tar():
    """The best tar on this machine, preferring the one libarchive built.

    Windows has shipped bsdtar as C:\\Windows\\System32\\tar.exe since 2018,
    and it reads 7-Zip and zstd with no helper binaries.  That is the whole
    reason this installer needs no dependencies, so it is looked for by full
    path first: a GNU tar earlier on PATH would otherwise win and then fail
    on the first archive.
    """
    candidates = []
    if os.name == "nt":
        candidates.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                       "System32", "tar.exe"))
    found = shutil.which("tar")
    if found:
        candidates.append(found)

    fallback = None
    for path in candidates:
        if not os.path.isfile(path):
            continue
        is_bsdtar, has_zstd = _tar_flavour(path)
        if is_bsdtar and has_zstd:
            return path
        fallback = fallback or path
    return fallback


def _run_tar(tar, archive, dest, kind):
    """Unpack one archive, or raise InstallError saying which tar failed."""
    if not tar:
        raise InstallError(
            "No tar was found on this machine, so " + kind + " cannot be "
            "unpacked. Install one, or unpack " + os.path.basename(archive)
            + " by hand.")
    done = subprocess.run([tar, "-xf", archive, "-C", dest],
                          capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or "").strip().splitlines()
        raise InstallError(
            tar + " could not unpack the " + kind + " archive"
            + (": " + detail[0] if detail else ".")
            + "\nThis needs a tar that reads " + kind + "; Windows' own "
            "C:\\Windows\\System32\\tar.exe does.")


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


def _open(url):
    """GET a URL with a named agent, or raise InstallError about the network."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(request, timeout=120)
    except (urllib.error.URLError, OSError) as exc:
        raise InstallError(
            "Could not reach " + url.split("?")[0] + " (" + str(exc) + ").\n"
            "Check the network, or download it by hand and unpack it into "
            + toolchain.tools_root() + ".")


def _open_archive(url, hops=2):
    """Open a URL that should be an archive, through any landing page.

    SourceForge serves the file itself to some clients and a "your download
    will start shortly" page to others; which one arrives depends on the
    user agent and has changed before.  So the content type decides: if HTML
    comes back, the mirror link is read out of that page and followed.
    Bounded, so a page that links to itself cannot loop.
    """
    response = _open(url)
    if "html" not in (response.headers.get("Content-Type") or "").lower():
        return response

    with response:
        page = response.read().decode("utf-8", "replace")
    match = re.search(
        r"https://downloads\.sourceforge\.net/project/[^\"'<> ]+\?[^\"'<> ]+",
        page)
    if not match or hops <= 0:
        raise InstallError(
            "That URL answered with a web page rather than the archive, and "
            "no download link in it was recognised.\nFetch it by hand from "
            + url + " and unpack it into " + toolchain.tools_root() + ".")
    return _open_archive(html.unescape(match.group(0)), hops - 1)


def _download(url, dest, expected_sha, expected_bytes, label):
    """Stream a URL to a file, showing progress, verifying the hash.

    The hash is computed on the way past and checked before the file is
    used for anything, so a truncated or substituted download is refused
    rather than unpacked.
    """
    digest = hashlib.sha256()
    seen = 0
    with _open_archive(url) as response:
        # What this response promised, which is not the same question as
        # what we pinned: one detects a mirror cutting off mid-stream, the
        # other detects a different file arriving intact.
        claimed = int(response.headers.get("Content-Length") or 0)
        with io.open(dest, "wb") as stream:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                stream.write(block)
                digest.update(block)
                seen += len(block)
                _progress(label, seen, claimed or expected_bytes)
    _progress(label, seen, claimed or expected_bytes, last=True)

    # Truncation first, because it is the more specific diagnosis: a short
    # read and a substituted file both fail the checksum, and only one of
    # them is worth worrying about.
    if claimed and seen != claimed:
        _discard(dest)
        raise InstallError(
            "The download stopped after %d of the %d bytes the server said "
            "it was sending, so it was discarded." % (seen, claimed))

    actual = digest.hexdigest()
    if actual != expected_sha:
        _discard(dest)
        raise InstallError(
            "The download did not match its pinned checksum, so it was "
            "discarded.\n  expected " + expected_sha + "\n  got      " + actual
            + "\nThis installer pins the exact file it was tested against. "
            "If it repeats, the published file has changed and this pin "
            "needs updating.")
    return dest


def _discard(path):
    """Remove a download that will not be trusted, quietly."""
    try:
        os.remove(path)
    except OSError:
        pass


def _fetch(spec, dest, label):
    """Download one pinned archive, giving a bad mirror a second chance.

    SourceForge hands each request to a different mirror and a mirror can
    truncate; that is what the checksum is for, and it means one bad one
    should cost a retry rather than the whole install. Nothing unverified
    is ever kept, on any attempt.
    """
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return _download(spec["url"], dest, spec["sha256"], spec["bytes"],
                             label)
        except InstallError as exc:
            last = exc
            if attempt < ATTEMPTS:
                print("  " + str(exc).splitlines()[0]
                      + " Trying again (%d of %d)." % (attempt + 1, ATTEMPTS))
    raise last


def _progress(label, seen, total, last=False):
    """One rewritten line of download progress, on a terminal only."""
    if not sys.stdout.isatty():
        return
    megabytes = seen / 1e6
    if total:
        line = "  %s  %.1f/%.1f MB  %3d%%" % (
            label, megabytes, total / 1e6, min(100, round(100.0 * seen / total)))
    else:
        line = "  %s  %.1f MB" % (label, megabytes)
    sys.stdout.write("\r" + line + "   ")
    sys.stdout.write("\n" if last else "")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# putting it in place
# ---------------------------------------------------------------------------


def _place(staged, dest):
    """Move a fully unpacked directory into place, replacing any old one.

    Staged beside the destination rather than in the temp directory, so the
    move is a rename on the same volume and a half-written install is never
    what the resolvers find.
    """
    try:
        if os.path.exists(dest):
            shutil.rmtree(dest)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        os.replace(staged, dest)
    except OSError as exc:
        raise InstallError(
            "The download was fine but could not be moved into " + dest
            + " (" + str(exc) + ").\nOn Windows this usually means something "
            "is using the old copy: stop the server and any running "
            "simulation, then run this again.")


def _staging(dest):
    """An empty directory beside dest, cleaned first if one was left behind."""
    staged = dest + ".partial"
    if os.path.exists(staged):
        shutil.rmtree(staged)
    os.makedirs(staged)
    return staged


# ---------------------------------------------------------------------------
# the two steps
# ---------------------------------------------------------------------------


def install_ngspice(force=False, tar=None):
    """Put a console ngspice under ~/.faradaem/tools, unless one resolves."""
    if not force:
        try:
            return PRESENT, "already resolves to " + runner.find_ngspice()
        except runner.NgspiceNotFoundError:
            pass

    if os.name != "nt":
        return UNAVAILABLE, (
            "no official prebuilt ngspice exists for this platform; install "
            "it with your package manager (apt install ngspice, brew install "
            "ngspice) and it will be found on PATH")

    dest = toolchain.ngspice_root()
    staged = _staging(dest)
    try:
        with tempfile.TemporaryDirectory(prefix="faradaem_install_") as work:
            archive = os.path.join(work, NGSPICE["archive"])
            _fetch(NGSPICE, archive, NGSPICE["archive"])
            _run_tar(tar, archive, staged, "7-Zip")
        probe = os.path.join(staged, *toolchain.NGSPICE_PARTS_WINDOWS)
        if not os.path.isfile(probe):
            raise InstallError(
                "The archive unpacked but did not contain "
                + os.path.join(*toolchain.NGSPICE_PARTS_WINDOWS)
                + ", so its layout has changed and this installer needs "
                "updating.")
        _place(staged, dest)
    finally:
        if os.path.exists(staged):
            shutil.rmtree(staged, ignore_errors=True)

    return INSTALLED, toolchain.ngspice_exe()


def install_pdk(force=False, tar=None):
    """Put the SKY130 technology files under ~/.faradaem/tools."""
    if not force and runner.sky130_available():
        return PRESENT, "already resolves to " + runner.sky130_lib_path()

    dest = toolchain.pdk_root()
    staged = _staging(dest)
    try:
        with tempfile.TemporaryDirectory(prefix="faradaem_install_") as work:
            for spec in PDK_ARCHIVES:
                archive = os.path.join(work, spec["archive"])
                _fetch(spec, archive, spec["archive"])
                _run_tar(tar, archive, staged, "zstd")
        _check_pdk_tree(staged)
        _record_version(staged, PDK_VERSION)
        _place(staged, dest)
    finally:
        if os.path.exists(staged):
            shutil.rmtree(staged, ignore_errors=True)

    return INSTALLED, toolchain.pdk_root()


def _check_pdk_tree(root):
    """Refuse a PDK tree that would fail at the first transistor.

    Checking that the model library is present is not enough, and finding
    that out cost an afternoon: the library loads, and then the corner file
    it pulls in includes the device models by relative path out of
    libs.tech, and a tree missing those stops with "could not find include
    file" on the first simulation rather than here. So the includes are
    resolved now, while there is still something useful to say about it.
    """
    library = os.path.join(root, *runner.SKY130_LIB_PARTS)
    if not os.path.isfile(library):
        raise InstallError(
            "The archives unpacked but did not contain "
            + os.path.join(*runner.SKY130_LIB_PARTS)
            + ", so their layout has changed and this installer needs "
            "updating.")

    corner = os.path.join(root, *CORNER_PROBE_PARTS)
    if not os.path.isfile(corner):
        raise InstallError(
            "The archives unpacked but did not contain "
            + os.path.join(*CORNER_PROBE_PARTS) + ", so their layout has "
            "changed and this installer needs updating.")

    missing = _unresolved_includes(corner)
    if missing:
        raise InstallError(
            "The typical corner includes %d file(s) that were not unpacked, "
            "starting with %s.\nThe set of archives this installer fetches "
            "no longer covers the models, and needs updating."
            % (len(missing), missing[0]))


def _unresolved_includes(path):
    """Include targets a SPICE file names that are not on disk beside it."""
    here = os.path.dirname(path)
    missing = []
    with io.open(path, "r", encoding="ascii", errors="replace") as stream:
        for line in stream:
            found = re.match(r"\s*\.include\s+(\S+)", line, re.IGNORECASE)
            if not found:
                continue
            target = found.group(1).strip("\"'")
            resolved = os.path.join(here, target.replace("/", os.sep))
            if not os.path.isfile(resolved):
                missing.append(target)
    return missing


def _record_version(root, version):
    """Write down which upstream release this tree came from.

    A full ciel install carries its version in a directory name; this one
    does not, and provenance that cannot say which models produced a number
    makes the number worth less. Written before the tree is moved into
    place, so an install is never findable without its version.
    """
    with io.open(os.path.join(root, toolchain.VERSION_FILE), "w",
                 encoding="ascii") as stream:
        stream.write(version + "\n")


STEPS = (("ngspice", install_ngspice), ("SKY130 PDK", install_pdk))


def install(force=False, tar=None):
    """Run every step in order. Returns a list of dicts, one per step."""
    if tar is None:
        tar = find_tar()
    done = []
    for name, step in STEPS:
        print("[..] " + name)
        try:
            state, detail = step(force=force, tar=tar)
        except InstallError as exc:
            state, detail = FAILED, str(exc)
        except OSError as exc:
            # A backstop, so a full disk or a permission problem reads as a
            # failed step with its reason rather than as a traceback.
            state, detail = FAILED, ("could not write under "
                                     + toolchain.tools_root()
                                     + " (" + str(exc) + ")")
        done.append({"name": name, "state": state, "detail": detail})
    return done


# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------


def describe():
    """What --list prints: everything that would be fetched, and where to."""
    wanted = [(NGSPICE, toolchain.ngspice_root())]
    wanted += [(spec, toolchain.pdk_root()) for spec in PDK_ARCHIVES]
    total = sum(spec["bytes"] for spec, _ in wanted)

    lines = ["Faradaem would fetch %d archives, %.1f MB in all, into %s"
             % (len(wanted), total / 1e6, toolchain.tools_root()), ""]
    for spec, into in wanted:
        lines += [
            "  " + spec["what"],
            "    from   " + spec["url"],
            "    into   " + into,
            "    size   %.1f MB" % (spec["bytes"] / 1e6),
            "    sha256 " + spec["sha256"],
            "",
        ]
    tar = find_tar()
    lines.append("  unpacked with " + (tar or "no tar found on this machine"))
    lines.append("")
    lines.append("Nothing is installed with pip and nothing goes on PATH.")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="install.py",
        description="Install the simulator and PDK Faradaem needs.")
    parser.add_argument("--list", action="store_true",
                        help="say what would be fetched and fetch nothing")
    parser.add_argument("--force", action="store_true",
                        help="fetch again even when something is already there")
    args = parser.parse_args(argv)

    if args.list:
        print(describe())
        return 0

    done = install(force=args.force)

    print()
    width = max(len(item["name"]) for item in done)
    for item in done:
        head, _, rest = item["detail"].partition("\n")
        print("[%s] %-*s  %s" % (MARKS[item["state"]], width,
                                 item["name"], head))
        for line in rest.splitlines():
            print(" " * (width + 7) + line)

    failures = [item for item in done if item["state"] == FAILED]
    print()
    if failures:
        print("%d step(s) did not finish. Everything else is in place; the "
              "features that need the missing piece will say so." % len(failures))
    else:
        print("The toolchain is in place. Checking what the tool itself sees:")
        print()
        doctor.main()
    return len(failures)


if __name__ == "__main__":
    sys.exit(main())
