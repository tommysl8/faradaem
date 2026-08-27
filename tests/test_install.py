"""The one-command toolchain install, and where it puts things.

Nothing here touches the network. The installer's two risky moments -- a
landing page arriving where an archive was expected, and a download that
does not match its pin -- are driven with stubbed responses, because those
are exactly the paths that never run on a good day and must still be right.
"""

import io
import os

import pytest

import install
from spice import runner, toolchain


# ---------------------------------------------------------------------------
# where things go
# ---------------------------------------------------------------------------


def test_home_defaults_outside_the_project(monkeypatch):
    """The project syncs; a simulator and a PDK do not belong in it."""
    monkeypatch.delenv(toolchain.HOME_ENV_VAR, raising=False)
    project = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.abspath(toolchain.home()).startswith(
        os.path.abspath(project))
    assert toolchain.home().endswith(".faradaem")


def test_the_environment_can_move_the_home(tmp_path, monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    assert toolchain.home() == str(tmp_path)
    assert toolchain.tools_root().startswith(str(tmp_path))
    assert toolchain.pdk_root().startswith(str(tmp_path))
    assert toolchain.ngspice_exe().startswith(str(tmp_path))


def test_a_blank_home_value_is_ignored(monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, "   ")
    assert toolchain.home().endswith(".faradaem")


def test_the_ledger_shares_the_home(tmp_path, monkeypatch):
    """One root, so the records and the tools cannot drift apart."""
    from spice import ledger

    monkeypatch.delenv(ledger.LEDGER_ENV_VAR, raising=False)
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    assert ledger.root() == os.path.join(str(tmp_path), "ledger")


def test_nothing_is_reported_installed_until_it_is_there(tmp_path, monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    assert toolchain.managed_ngspice() is None
    assert toolchain.managed_pdk(runner.SKY130_LIB_PARTS) is None

    exe = toolchain.ngspice_exe()
    os.makedirs(os.path.dirname(exe))
    with io.open(exe, "w", encoding="ascii") as stream:
        stream.write("not really a simulator")
    assert toolchain.managed_ngspice() == exe


# ---------------------------------------------------------------------------
# resolution finds what the installer wrote
# ---------------------------------------------------------------------------


def _plant_ngspice(tmp_path, monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    exe = toolchain.ngspice_exe()
    os.makedirs(os.path.dirname(exe))
    with io.open(exe, "w", encoding="ascii") as stream:
        stream.write("stand-in")
    return exe


def test_an_installed_simulator_is_found_with_no_configuration(tmp_path,
                                                               monkeypatch):
    monkeypatch.delenv(runner.NGSPICE_ENV_VAR, raising=False)
    exe = _plant_ngspice(tmp_path, monkeypatch)
    assert runner.find_ngspice() == exe


def test_an_installed_simulator_outranks_one_on_the_path(tmp_path, monkeypatch):
    """The installed copy is the pinned one, so it is the one believed."""
    monkeypatch.delenv(runner.NGSPICE_ENV_VAR, raising=False)
    exe = _plant_ngspice(tmp_path, monkeypatch)
    monkeypatch.setattr("shutil.which", lambda name: r"D:\elsewhere\ngspice_con.exe")
    assert runner.find_ngspice() == exe


def test_the_environment_outranks_an_installed_simulator(tmp_path, monkeypatch,
                                                         request):
    _plant_ngspice(tmp_path, monkeypatch)
    mine = tmp_path / "mine.exe"
    mine.write_text("stand-in", encoding="ascii")
    monkeypatch.setenv(runner.NGSPICE_ENV_VAR, str(mine))
    assert runner.find_ngspice() == str(mine)


def test_the_failure_names_every_attempt_and_the_installer(tmp_path,
                                                           monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setenv(runner.NGSPICE_ENV_VAR, str(tmp_path / "nope.exe"))
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("spice.runner.NGSPICE_FALLBACK_PATH",
                        str(tmp_path / "also-nope.exe"))

    with pytest.raises(runner.NgspiceNotFoundError) as excinfo:
        runner.find_ngspice()

    message = str(excinfo.value)
    for step in ("1.", "2.", "3.", "4."):
        assert step in message
    assert "install.py" in message


# ---------------------------------------------------------------------------
# what --list promises
# ---------------------------------------------------------------------------


def _all_archives():
    return (install.NGSPICE,) + tuple(install.PDK_ARCHIVES)


def test_every_download_is_pinned():
    for spec in _all_archives():
        assert len(spec["sha256"]) == 64
        assert set(spec["sha256"]) <= set("0123456789abcdef")
        assert spec["bytes"] > 0
        assert spec["url"].startswith("https://")


def test_the_listing_says_what_it_would_fetch_and_where(tmp_path, monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    text = install.describe()
    for spec in _all_archives():
        assert spec["url"] in text
        assert spec["sha256"] in text
    assert str(tmp_path) in text
    # The reader is told what unpacks it, and that nothing else is touched.
    assert "pip" in text


def test_the_pdk_urls_carry_the_version_that_gets_recorded():
    """One version string, so the download and the provenance agree."""
    assert len(install.PDK_VERSION) == 40
    for spec in install.PDK_ARCHIVES:
        assert install.PDK_VERSION in spec["url"]


def test_the_models_are_fetched_as_well_as_the_technology_files():
    """The technology archive alone loads and then stops at the first
    transistor, which is invisible until one appears."""
    names = [spec["archive"] for spec in install.PDK_ARCHIVES]
    assert "common.tar.zst" in names
    assert "sky130_fd_pr.tar.zst" in names


def test_an_install_records_which_release_it_came_from(tmp_path):
    """A run whose PDK version is unknown is worth less as evidence."""
    root = tmp_path / "pdk"
    root.mkdir()
    install._record_version(str(root), install.PDK_VERSION)
    assert toolchain.recorded_version(str(root)) == install.PDK_VERSION


def test_a_tree_with_no_marker_says_unknown_rather_than_guessing(tmp_path):
    assert toolchain.recorded_version(str(tmp_path)) is None


def test_the_ledger_reads_the_recorded_version(tmp_path, monkeypatch):
    """A full ciel install carries its version in a directory name; this
    one does not, so the two paths have to reach the same answer."""
    from spice import ledger

    library = tmp_path.joinpath(*runner.SKY130_LIB_PARTS)
    library.parent.mkdir(parents=True)
    library.write_text("* a stand-in library\n", encoding="ascii")
    install._record_version(str(tmp_path), install.PDK_VERSION)
    monkeypatch.setenv("PDK_ROOT", str(tmp_path))

    assert ledger._pdk()["version"] == install.PDK_VERSION


def test_the_pdk_download_stays_a_small_fraction_of_a_full_install():
    """A full SKY130 install is 2.2 GB; almost all of it is standard cells,
    IO pads and SRAM macros that an analog tool never opens."""
    assert sum(spec["bytes"] for spec in install.PDK_ARCHIVES) < 30e6


# ---------------------------------------------------------------------------
# the tree is checked before it is accepted
# ---------------------------------------------------------------------------


def _plant_pdk_tree(root, includes=()):
    """A stand-in PDK: the library, a corner file, and what it includes."""
    library = root.joinpath(*runner.SKY130_LIB_PARTS)
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text("* a stand-in library\n", encoding="ascii")

    corner = root.joinpath(*install.CORNER_PROBE_PARTS)
    corner.parent.mkdir(parents=True, exist_ok=True)
    corner.write_text(
        "".join(".include " + name + "\n" for name in includes),
        encoding="ascii")
    return corner


def test_a_tree_whose_corner_includes_resolve_is_accepted(tmp_path):
    # Three levels up from sky130A/libs.tech/ngspice/corners is sky130A,
    # which is where the real corner files reach for the device models.
    corner = _plant_pdk_tree(tmp_path, ["../../../libs.ref/models.spice"])
    target = tmp_path / "sky130A" / "libs.ref" / "models.spice"
    target.parent.mkdir(parents=True)
    target.write_text("* models\n", encoding="ascii")

    install._check_pdk_tree(str(tmp_path))
    assert install._unresolved_includes(str(corner)) == []


def test_a_tree_missing_the_models_is_refused_at_install_time(tmp_path):
    """Otherwise it loads fine and stops at the first transistor, which is
    someone's first simulation rather than their install."""
    _plant_pdk_tree(tmp_path, ["../../../libs.ref/sky130_fd_pr/nfet.spice"])

    with pytest.raises(install.InstallError) as excinfo:
        install._check_pdk_tree(str(tmp_path))
    message = str(excinfo.value)
    assert "nfet.spice" in message
    assert "installer" in message


def test_a_tree_with_no_library_at_all_is_refused(tmp_path):
    with pytest.raises(install.InstallError) as excinfo:
        install._check_pdk_tree(str(tmp_path))
    assert "sky130.lib.spice" in str(excinfo.value)


def test_a_tree_with_no_corner_file_is_refused(tmp_path):
    library = tmp_path.joinpath(*runner.SKY130_LIB_PARTS)
    library.parent.mkdir(parents=True)
    library.write_text("* a stand-in library\n", encoding="ascii")

    with pytest.raises(install.InstallError) as excinfo:
        install._check_pdk_tree(str(tmp_path))
    assert "tt.spice" in str(excinfo.value)


# ---------------------------------------------------------------------------
# the verdicts
# ---------------------------------------------------------------------------


def test_every_state_has_a_mark():
    states = (install.INSTALLED, install.PRESENT, install.UNAVAILABLE,
              install.FAILED)
    assert set(install.MARKS) == set(states)
    assert len(set(states)) == 4


def test_a_platform_with_no_build_says_so_rather_than_failing(tmp_path,
                                                              monkeypatch):
    """Absent is not the same as broken, and neither is dressed up as ok."""
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(install.os, "name", "posix")
    monkeypatch.setattr(install.runner, "find_ngspice",
                        _raiser(runner.NgspiceNotFoundError("none here")))

    state, detail = install.install_ngspice(force=False, tar=None)
    assert state == install.UNAVAILABLE
    assert "package manager" in detail


def test_an_already_working_simulator_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(install.runner, "find_ngspice",
                        lambda: r"D:\somewhere\ngspice_con.exe")
    state, detail = install.install_ngspice(force=False, tar=None)
    assert state == install.PRESENT
    assert r"D:\somewhere\ngspice_con.exe" in detail


def test_an_already_present_pdk_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv(toolchain.HOME_ENV_VAR, str(tmp_path))
    monkeypatch.setattr(install.runner, "sky130_available", lambda: True)
    monkeypatch.setattr(install.runner, "sky130_lib_path",
                        lambda: r"D:\pdk\sky130.lib.spice")
    state, detail = install.install_pdk(force=False, tar=None)
    assert state == install.PRESENT


def _raiser(error):
    def raise_it(*args, **kwargs):
        raise error
    return raise_it


# ---------------------------------------------------------------------------
# fetching, with the network stubbed
# ---------------------------------------------------------------------------


class FakeResponse(object):
    """Enough of an http response for _open_archive and _download."""

    def __init__(self, body, content_type):
        self._stream = io.BytesIO(body)
        self.headers = {"Content-Type": content_type,
                        "Content-Length": str(len(body))}

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_a_landing_page_is_followed_to_the_archive(monkeypatch):
    """SourceForge sometimes answers with a page; the mirror link is in it."""
    mirror = ("https://downloads.sourceforge.net/project/ngspice/"
              "ngspice-47_64.7z?ts=abc&amp;use_mirror=example")
    page = ('<html><a href="' + mirror + '">direct link</a></html>').encode()
    served = {}

    def fake_open(url):
        served["last"] = url
        if "downloads.sourceforge.net" in url:
            return FakeResponse(b"7z-ish", "application/x-7z-compressed")
        return FakeResponse(page, "text/html; charset=utf-8")

    monkeypatch.setattr(install, "_open", fake_open)
    with install._open_archive("https://sourceforge.net/whatever") as response:
        assert response.read() == b"7z-ish"
    # The &amp; in the page's href must be unescaped before it is fetched.
    assert "&amp;" not in served["last"]
    assert "use_mirror=example" in served["last"]


def test_a_page_with_no_link_says_what_to_do_by_hand(monkeypatch):
    monkeypatch.setattr(install, "_open",
                        lambda url: FakeResponse(b"<html>nothing</html>",
                                                 "text/html"))
    with pytest.raises(install.InstallError) as excinfo:
        install._open_archive("https://sourceforge.net/whatever")
    assert "by hand" in str(excinfo.value)


def test_a_download_that_misses_its_pin_is_discarded(tmp_path, monkeypatch):
    """A binary that is about to be executed is not taken on trust."""
    monkeypatch.setattr(install, "_open",
                        lambda url: FakeResponse(b"not what was pinned",
                                                 "application/octet-stream"))
    dest = tmp_path / "archive.7z"
    with pytest.raises(install.InstallError) as excinfo:
        install._download("https://example.invalid/a.7z", str(dest),
                          "0" * 64, 19, "a.7z")

    assert "checksum" in str(excinfo.value)
    assert not dest.exists()


def test_a_short_download_is_named_as_truncated(tmp_path, monkeypatch):
    """A short read and a substituted file both fail the checksum, and only
    one of them is worth worrying about."""
    class ShortResponse(FakeResponse):
        def __init__(self):
            FakeResponse.__init__(self, b"half", "application/octet-stream")
            self.headers["Content-Length"] = "999"

    monkeypatch.setattr(install, "_open", lambda url: ShortResponse())
    dest = tmp_path / "archive.7z"
    with pytest.raises(install.InstallError) as excinfo:
        install._download("https://example.invalid/a.7z", str(dest),
                          "0" * 64, 0, "a.7z")

    assert "stopped after" in str(excinfo.value)
    assert not dest.exists()


def test_one_bad_mirror_costs_a_retry_not_the_install(tmp_path, monkeypatch):
    import hashlib

    body = b"the real archive"
    served = {"n": 0}

    def fake_open(url):
        served["n"] += 1
        if served["n"] == 1:
            return FakeResponse(b"a different file entirely",
                                "application/octet-stream")
        return FakeResponse(body, "application/octet-stream")

    monkeypatch.setattr(install, "_open", fake_open)
    spec = {"url": "https://example.invalid/a.7z", "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest()}
    dest = tmp_path / "archive.7z"
    install._fetch(spec, str(dest), "a.7z")

    assert dest.read_bytes() == body
    assert served["n"] == 2


def test_retries_run_out_and_the_last_failure_is_reported(tmp_path,
                                                          monkeypatch):
    """A retry can never turn into accepting the wrong file."""
    monkeypatch.setattr(install, "_open",
                        lambda url: FakeResponse(b"never right",
                                                 "application/octet-stream"))
    spec = {"url": "https://example.invalid/a.7z", "bytes": 11,
            "sha256": "0" * 64}
    dest = tmp_path / "archive.7z"
    with pytest.raises(install.InstallError) as excinfo:
        install._fetch(spec, str(dest), "a.7z")

    assert "checksum" in str(excinfo.value)
    assert not dest.exists()


def test_a_download_that_matches_its_pin_is_kept(tmp_path, monkeypatch):
    import hashlib

    body = b"exactly what was pinned"
    monkeypatch.setattr(install, "_open",
                        lambda url: FakeResponse(body, "application/octet-stream"))
    dest = tmp_path / "archive.7z"
    install._download("https://example.invalid/a.7z", str(dest),
                      hashlib.sha256(body).hexdigest(), len(body), "a.7z")
    assert dest.read_bytes() == body


# ---------------------------------------------------------------------------
# the server says at startup what would otherwise fail at the first click
# ---------------------------------------------------------------------------


def test_the_server_warns_about_what_is_missing(monkeypatch, capsys):
    import server

    monkeypatch.setattr(server.runner, "find_ngspice",
                        _raiser(runner.NgspiceNotFoundError("none")))
    monkeypatch.setattr(server.runner, "sky130_available", lambda: False)
    server._startup_warnings()

    printed = capsys.readouterr().out
    assert "No simulator found" in printed
    assert "No SKY130 kit found" in printed
    assert printed.count("install.py") == 2


def test_the_server_says_nothing_when_the_machine_is_set_up(monkeypatch,
                                                            capsys):
    import server

    monkeypatch.setattr(server.runner, "find_ngspice", lambda: "ngspice")
    monkeypatch.setattr(server.runner, "sky130_available", lambda: True)
    server._startup_warnings()

    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# unpacking with the tar already on the machine
# ---------------------------------------------------------------------------


def test_a_locked_old_copy_is_explained_rather_than_traced(tmp_path,
                                                           monkeypatch):
    """On Windows the old simulator is often still open somewhere."""
    staged = tmp_path / "new"
    staged.mkdir()
    dest = tmp_path / "installed"
    dest.mkdir()

    def refuse(path):
        raise OSError(32, "The process cannot access the file")

    monkeypatch.setattr(install.shutil, "rmtree", refuse)
    with pytest.raises(install.InstallError) as excinfo:
        install._place(str(staged), str(dest))
    assert "stop the server" in str(excinfo.value)


def test_a_missing_tar_is_reported_rather_than_crashed():
    with pytest.raises(install.InstallError) as excinfo:
        install._run_tar(None, "somewhere.7z", "dest", "7-Zip")
    assert "No tar" in str(excinfo.value)


def test_the_tar_that_is_found_can_read_what_we_download():
    """The whole no-dependencies claim rests on this one binary."""
    tar = install.find_tar()
    if not tar:
        pytest.skip("no tar on this machine")
    is_bsdtar, has_zstd = install._tar_flavour(tar)
    if os.name != "nt":
        pytest.skip("only Windows is guaranteed to ship libarchive's tar")
    assert is_bsdtar and has_zstd
