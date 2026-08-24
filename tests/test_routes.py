"""HTTP surface of the server: the GET whitelist, 404s, and POST /simulate.

The pure routing logic (resolve_route) is tested directly; the rest runs against
a real FaradaemHandler bound to an ephemeral port, so the request line reaches
the handler exactly as written -- http.client does not normalise paths, which is
what lets the traversal test be honest.
"""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import server
from spice.runner import NgspiceRunError, find_ngspice

HTML = "text/html; charset=utf-8"
JSON = "application/json; charset=utf-8"


@pytest.fixture(scope="module")
def address():
    """Run the real handler on a throwaway port for the duration of the module."""
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.FaradaemHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[0], httpd.server_address[1]
    finally:
        httpd.shutdown()
        thread.join(timeout=10)
        httpd.server_close()


def fetch(address, path, method="GET", body=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    conn = http.client.HTTPConnection(address[0], address[1], timeout=30)
    try:
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        return (
            response.status,
            response.getheader("Content-Type"),
            response.read().decode("utf-8", "replace"),
        )
    finally:
        conn.close()


# ---- pure routing logic -------------------------------------------------


@pytest.mark.parametrize("path", sorted(server.ROUTES))
def test_resolve_route_returns_a_file_and_type(path):
    relative_file, content_type = server.resolve_route(path)
    assert relative_file
    assert content_type


@pytest.mark.parametrize(
    "path",
    [
        "/nope",
        "/index.html",
        "/static",
        "/static/",
        "/static/../server.py",
        "/../CLAUDE.md",
        "/static/style.css/",
        "/STATIC/style.css",
        "",
        "//",
    ],
)
def test_resolve_route_rejects_anything_not_whitelisted(path):
    assert server.resolve_route(path) is None


def test_route_targets_are_literals_not_derived_from_requests():
    for relative_file, _ in server.ROUTES.values():
        assert ".." not in relative_file
        assert not relative_file.startswith("/")


def test_every_route_target_exists_on_disk():
    for path, (relative_file, _) in sorted(server.ROUTES.items()):
        assert (server.PROJECT_ROOT / relative_file).is_file(), path


def test_the_pages_and_their_assets_are_served():
    """The GET surface is exactly this. Adding to it is a deliberate act."""
    assert set(server.ROUTES) == {
        "/",
        "/manual",
        "/about",
        "/changelog",
        "/static/style.css",
        "/static/app.js",
        "/static/schematic.js",
        "/static/bodeplot.js",
        "/favicon.svg",
        "/favicon.ico",
        "/static/icon.svg",
        "/static/icon-32.png",
        "/static/apple-touch-icon.png",
        "/static/og.png",
    }


def test_server_stays_quiet_when_a_client_hangs_up(capsys):
    """A closed tab is normal network life, not something to print a stack for."""
    httpd = server.FaradaemServer.__new__(server.FaradaemServer)
    try:
        raise ConnectionResetError("the browser went away")
    except ConnectionResetError:
        httpd.handle_error(None, ("127.0.0.1", 0))
    assert "Traceback" not in capsys.readouterr().err


def test_a_real_fault_is_still_reported(capsys):
    httpd = server.FaradaemServer.__new__(server.FaradaemServer)
    try:
        raise RuntimeError("something actually broke")
    except RuntimeError:
        httpd.handle_error(None, ("127.0.0.1", 0))
    assert "something actually broke" in capsys.readouterr().err


def test_favicon_is_served_as_svg(address):
    status, content_type, body = fetch(address, "/favicon.svg")
    assert status == 200
    assert content_type == "image/svg+xml"
    assert body.startswith("<svg")
    assert "#FFFFFF" in body  # the white AE mark, not a default page


@pytest.mark.parametrize("path", ["/", "/manual", "/about", "/changelog"])
def test_pages_link_the_real_icons(address, path):
    _, _, body = fetch(address, path)
    assert '<link rel="icon" href="/static/icon.svg" type="image/svg+xml">' in body
    assert '<link rel="icon" href="/static/icon-32.png" sizes="32x32"' in body
    assert '<link rel="apple-touch-icon" href="/static/apple-touch-icon.png">' in body


@pytest.mark.parametrize(
    "path,title",
    [("/", "<title>Faradaem: AI-assisted analog IC design</title>"),
     ("/manual", "<title>Manual - Faradaem</title>"),
     ("/changelog", "<title>Changelog - Faradaem</title>"),
     ("/about", "<title>About - Faradaem</title>")],
)
def test_pages_have_their_own_title_and_description(address, path, title):
    _, _, body = fetch(address, path)
    assert title in body
    assert '<meta name="description"' in body


@pytest.mark.parametrize("path", ["/", "/manual", "/about", "/changelog"])
def test_pages_use_no_inline_style_attributes(address, path):
    _, _, body = fetch(address, path)
    assert "style=" not in body


# ---- whitelisted GETs over HTTP ----------------------------------------


@pytest.mark.parametrize("path", sorted(server.ROUTES))
def test_whitelisted_route_returns_200_with_expected_content_type(address, path):
    expected_type = server.ROUTES[path][1]
    status, content_type, body = fetch(address, path)
    assert status == 200
    assert content_type == expected_type
    assert body.strip()


@pytest.mark.parametrize("path", ["/", "/manual", "/about", "/changelog"])
def test_pages_share_the_shell(address, path):
    _, _, body = fetch(address, path)
    assert 'FARAD<span class="wordmark-ae">&AElig;</span>M<span class="wordmark-tm">&trade;</span>' in body
    assert '/static/style.css' in body
    assert 'href="/manual"' in body
    assert 'href="/about"' in body
    assert 'href="/changelog"' in body
    assert "github.com/tommysl8/faradaem" in body


@pytest.mark.parametrize("path", ["/", "/manual", "/about", "/changelog"])
def test_pages_carry_no_inline_script_or_style(address, path):
    _, _, body = fetch(address, path)
    assert "<script>" not in body
    assert "<style>" not in body


def test_simulator_page_loads_both_scripts(address):
    _, _, body = fetch(address, "/")
    assert "/static/schematic.js" in body
    assert "/static/app.js" in body


def test_stylesheet_defines_the_design_tokens(address):
    _, _, body = fetch(address, "/static/style.css")
    for token in (
        "--ink",
        "--panel",
        "--line",
        "--text",
        "--muted",
        "--accent",
        "--accent-hover",
        "--line-strong",
        # Figures are drawn on the dark ground: grey wires, cyan nodes.
        "--wire",
        "--ok",
        "--err",
    ):
        assert token in body


#: Outbound links the author put on the page. These are navigations the reader
#: chooses, not resources the page fetches, so they do not make the frontend
#: depend on anything. Every other https:// is a bug.
OUTBOUND_LINKS = (
    "https://github.com/tommysl8/faradaem",
    "https://www.linkedin.com/in/tommysliu/",
    "https://x.com/tommysliu",
)


def test_frontend_pulls_nothing_from_the_network(address):
    for path in ("/", "/manual", "/about", "/changelog", "/static/style.css",
                 "/static/app.js"):
        _, _, body = fetch(address, path)
        assert "//fonts.googleapis" not in body
        assert "cdn." not in body
        assert "@import" not in body

        # The asset surfaces specifically: a remote script, image or font.
        assert 'src="http' not in body
        assert "url(http" not in body

        remaining = body
        for link in OUTBOUND_LINKS:
            remaining = remaining.replace(link, "")
        assert "https://" not in remaining


# ---- 404s ---------------------------------------------------------------


def test_unknown_path_returns_404_json(address):
    status, content_type, body = fetch(address, "/nope")
    assert status == 404
    assert content_type == JSON
    assert json.loads(body)["error"].startswith("Not found")


def test_traversal_attempt_returns_404(address):
    status, content_type, body = fetch(address, "/static/../server.py")
    assert status == 404
    assert content_type == JSON
    assert "def main" not in body


@pytest.mark.parametrize(
    "path", ["/static/../../CLAUDE.md", "/static/style.css/../app.js", "/spice/runner.py"]
)
def test_other_traversal_shapes_return_404(address, path):
    status, _, _ = fetch(address, path)
    assert status == 404


def test_post_to_an_unknown_path_returns_404(address):
    status, content_type, _ = fetch(address, "/elsewhere", "POST", json.dumps({}))
    assert status == 404
    assert content_type == JSON


def test_rejected_post_does_not_desync_a_keep_alive_connection(address):
    """A 404 must still consume the body, or the next request parses garbage."""
    conn = http.client.HTTPConnection(address[0], address[1], timeout=30)
    try:
        conn.request(
            "POST", "/elsewhere", body=json.dumps({"vdd": 5}),
            headers={"Content-Type": "application/json"},
        )
        first = conn.getresponse()
        first.read()
        assert first.status == 404

        # Same socket, second request. Before the body was drained this came
        # back as "Bad request syntax" because the JSON was read as a verb.
        conn.request("GET", "/")
        second = conn.getresponse()
        body = second.read().decode("utf-8")
        assert second.status == 200
        assert 'FARAD<span class="wordmark-ae">&AElig;</span>M<span class="wordmark-tm">&trade;</span>' in body
    finally:
        conn.close()


# ---- POST /simulate is unchanged ---------------------------------------


def test_simulate_rejects_a_bad_body_with_400(address):
    status, content_type, body = fetch(
        address, "/simulate", "POST", json.dumps({"vdd": 5, "r1": 0, "r2": 10000})
    )
    assert status == 400
    assert content_type == JSON
    assert "r1" in json.loads(body)["error"]


def test_simulate_rejects_malformed_json_with_400(address):
    status, _, body = fetch(address, "/simulate", "POST", "{not json")
    assert status == 400
    assert "error" in json.loads(body)


def ngspice_missing():
    try:
        find_ngspice()
    except Exception:  # noqa: BLE001 - any discovery failure means "skip"
        return True
    return False


requires_ngspice = pytest.mark.skipif(
    ngspice_missing(), reason="ngspice is not available, so the live simulate tests cannot run"
)


# ---- POST /simulate_ac --------------------------------------------------


def post_ac(address, body):
    return fetch(address, "/simulate_ac", "POST", json.dumps(body))


@pytest.mark.parametrize(
    "body,expected_fragment",
    [
        ({"c": 1e-7}, "'r'"),
        ({"r": 1000}, "'c'"),
        ({}, "'r'"),
        ({"r": "abc", "c": 1e-7}, "'r'"),
        ({"r": None, "c": 1e-7}, "'r'"),
        ({"r": 1000, "c": True}, "'c'"),
        ({"r": 0, "c": 1e-7}, "'r'"),
        ({"r": -1000, "c": 1e-7}, "'r'"),
        ({"r": 1000, "c": 0}, "'c'"),
        ({"r": 1000, "c": -1e-7}, "'c'"),
        ({"r": 1e-9, "c": 1e-7}, "'r'"),
        ({"r": 1e15, "c": 1e-7}, "'r'"),
        ({"r": 1000, "c": 1e-20}, "'c'"),
        ({"r": 1000, "c": 5.0}, "'c'"),
        ({"r": float("1e999"), "c": 1e-7}, "'r'"),
    ],
)
def test_simulate_ac_rejects_bad_input(address, body, expected_fragment):
    status, content_type, payload = post_ac(address, body)
    assert status == 400
    assert content_type == JSON
    assert expected_fragment in json.loads(payload)["error"]


def test_simulate_ac_rejects_a_non_object_body(address):
    status, _, payload = fetch(address, "/simulate_ac", "POST", json.dumps([1000, 1e-7]))
    assert status == 400
    assert "JSON object" in json.loads(payload)["error"]


def test_simulate_ac_rejects_malformed_json(address):
    status, _, payload = fetch(address, "/simulate_ac", "POST", "{not json")
    assert status == 400
    assert "error" in json.loads(payload)


def test_validate_ac_request_accepts_a_good_body():
    assert server.validate_ac_request({"r": 1000, "c": 1.59e-7}) == (1000.0, 1.59e-7)


def test_validate_ac_request_accepts_numeric_strings():
    assert server.validate_ac_request({"r": "1e3", "c": "1e-7"}) == (1000.0, 1e-7)


def test_validate_ac_request_accepts_the_range_boundaries():
    low_r, high_r, _ = server.AC_LIMITS["r"]
    low_c, high_c, _ = server.AC_LIMITS["c"]
    assert server.validate_ac_request({"r": low_r, "c": low_c}) == (low_r, low_c)
    assert server.validate_ac_request({"r": high_r, "c": high_c}) == (high_r, high_c)


@requires_ngspice
def test_simulate_ac_returns_the_expected_shape(address):
    status, content_type, body = post_ac(address, {"r": 1000, "c": 1.59e-7})
    assert status == 200
    assert content_type == JSON

    payload = json.loads(body)
    assert set(payload) == {
        "freq", "mag_db", "phase_deg", "f3db", "fc_analytic",
        "dc_gain_db", "phase_at_f3db",
    }
    assert len(payload["freq"]) == len(payload["mag_db"]) == len(payload["phase_deg"])
    assert payload["f3db"] == pytest.approx(1000.9, rel=0.02)
    assert payload["fc_analytic"] == pytest.approx(1000.9745, rel=1e-4)
    assert payload["dc_gain_db"] == pytest.approx(0.0, abs=0.09)
    assert payload["phase_at_f3db"] == pytest.approx(-45.0, abs=2.0)


@requires_ngspice
def test_both_analyses_work_over_one_connection(address):
    """The DC path must not regress now that a second analysis exists."""
    status, _, dc_body = fetch(
        address, "/simulate", "POST", json.dumps({"vdd": 5, "r1": 10000, "r2": 10000})
    )
    assert status == 200
    assert json.loads(dc_body)["vout"] == pytest.approx(2.5, abs=1e-6)

    status, _, ac_body = post_ac(address, {"r": 1000, "c": 1.59e-7})
    assert status == 200
    assert json.loads(ac_body)["f3db"] == pytest.approx(1000.9, rel=0.02)

    status, _, dc_again = fetch(
        address, "/simulate", "POST", json.dumps({"vdd": 5, "r1": 10000, "r2": 10000})
    )
    assert status == 200
    assert json.loads(dc_again)["vout"] == pytest.approx(2.5, abs=1e-6)


# ---- GET /api/circuits --------------------------------------------------


def test_catalogue_endpoint_lists_every_circuit(address):
    status, content_type, body = fetch(address, "/api/circuits")
    assert status == 200
    assert content_type == JSON

    listing = json.loads(body)["circuits"]
    assert [item["id"] for item in listing] == [
        "divider", "rc_lowpass", "rc_highpass", "rlc_bandpass",
        "inverting_amp", "twopole_amp", "nfet_cs_amp", "opamp_two_stage",
        "ota_5t",
    ]


def test_catalogue_entries_carry_what_the_ui_needs(address):
    _, _, body = fetch(address, "/api/circuits")
    for entry in json.loads(body)["circuits"]:
        assert entry["name"] and entry["caption"]
        assert entry["analysis"] in ("dc", "ac")
        assert entry["params"]
        for spec in entry["params"]:
            assert set(spec) == {"key", "label", "unit", "default", "min", "max"}
        assert entry["readout"]["headline"]["key"]
        for item in entry["checks"]:
            assert "formula" not in item
            assert item["measured"] and item["tolerance"]


def test_catalogue_carries_presets_for_every_circuit(address):
    _, _, body = fetch(address, "/api/circuits")
    for entry in json.loads(body)["circuits"]:
        presets = entry["presets"]
        assert 2 <= len(presets) <= 6, entry["id"]

        keys = {spec["key"] for spec in entry["params"]}
        for item in presets:
            assert set(item) == {"label", "params"}
            assert item["label"]
            # A preset must fill in every parameter, or Run would send a hole.
            assert set(item["params"]) == keys, (entry["id"], item["label"])
            for value in item["params"].values():
                assert isinstance(value, float)


def test_every_preset_is_inside_its_own_parameter_limits(address):
    _, _, body = fetch(address, "/api/circuits")
    for entry in json.loads(body)["circuits"]:
        limits = {spec["key"]: spec for spec in entry["params"]}
        for item in entry["presets"]:
            for key, value in item["params"].items():
                spec = limits[key]
                assert spec["min"] <= value <= spec["max"], (entry["id"], item["label"], key)


# ---- POST /api/simulate -------------------------------------------------


def post_api(address, body):
    return fetch(address, "/api/simulate", "POST", json.dumps(body))


def defaults_body(circuit_id):
    return {"circuit": circuit_id, "params": server.circuits.defaults(circuit_id)}


@pytest.mark.parametrize(
    "body,fragment",
    [
        ({"circuit": "nope", "params": {}}, "Unknown circuit"),
        ({"params": {}}, "'circuit'"),
        ({"circuit": "", "params": {}}, "'circuit'"),
        ({"circuit": 5, "params": {}}, "'circuit'"),
        ({"circuit": "divider", "params": {"vdd": 5, "r1": 10000}}, "'r2'"),
        ({"circuit": "divider", "params": []}, "'params'"),
        ({"circuit": "rlc_bandpass", "params": {"r": 10, "l": 1e-3}}, "'c'"),
        ({"circuit": "divider", "params": {"vdd": 5, "r1": 0, "r2": 1}}, "between"),
        ({"circuit": "divider", "params": {"vdd": 5, "r1": 1e15, "r2": 1}}, "between"),
        ({"circuit": "rc_lowpass", "params": {"r": 1000, "c": 5.0}}, "between"),
        ({"circuit": "inverting_amp",
          "params": {"rin": 1000, "rf": 10000, "a0": 1e5, "gbw": 1e11}}, "between"),
        ({"circuit": "divider", "params": {"vdd": "abc", "r1": 1, "r2": 1}}, "'vdd'"),
    ],
)
def test_api_simulate_rejects_bad_input(address, body, fragment):
    status, content_type, payload = post_api(address, body)
    assert status == 400
    assert content_type == JSON
    assert fragment in json.loads(payload)["error"]


@pytest.mark.parametrize(
    "body,fragment",
    [
        # In-range values that combine into something unsweepable. These raise
        # before ngspice starts, so this test needs no simulator.
        ({"circuit": "rc_lowpass", "params": {"r": 1e12, "c": 1.0}},
         "outside the sweepable range"),
        # A loop that never has gain never crosses 0 dB: no phase margin.
        ({"circuit": "twopole_amp",
          "params": {"rin": 1.0, "rf": 1e9, "a0": 100.0, "gbw": 1e6, "fp2": 1e5}},
         "never crosses 0 dB"),
    ],
)
def test_api_simulate_maps_impossible_combinations_to_400(address, body, fragment):
    """Values individually in range, jointly impossible: a 400, never a 500."""
    status, content_type, payload = post_api(address, body)
    assert status == 400
    assert content_type == JSON
    assert fragment in json.loads(payload)["error"]


def test_api_simulate_rejects_a_non_object_body(address):
    status, _, payload = post_api(address, ["divider"])
    assert status == 400
    assert "JSON object" in json.loads(payload)["error"]


def test_validate_api_request_returns_floats():
    circuit_id, params = server.validate_api_request(
        {"circuit": "divider", "params": {"vdd": 5, "r1": "1e4", "r2": 10000}}
    )
    assert circuit_id == "divider"
    assert params == {"vdd": 5.0, "r1": 10000.0, "r2": 10000.0}
    assert all(isinstance(value, float) for value in params.values())


def test_validate_api_request_accepts_spec_boundaries():
    spec = {item["key"]: item for item in server.circuits.CIRCUITS["divider"]["params"]}
    _, params = server.validate_api_request({
        "circuit": "divider",
        "params": {"vdd": 5, "r1": spec["r1"]["min"], "r2": spec["r2"]["max"]},
    })
    assert params["r1"] == spec["r1"]["min"]
    assert params["r2"] == spec["r2"]["max"]


def test_api_simulate_reports_a_simulation_failure_as_500(address, monkeypatch):
    """A simulator failure is a 500 with a message, never a traceback."""
    def boom(circuit_id, params):
        raise NgspiceRunError("ngspice fell over")

    monkeypatch.setattr(server.circuits, "simulate", boom)
    status, content_type, payload = post_api(address, defaults_body("divider"))
    assert status == 500
    assert content_type == JSON
    assert "fell over" in json.loads(payload)["error"]


@requires_ngspice
@pytest.mark.parametrize(
    "circuit_id", ["divider", "rc_lowpass", "rc_highpass", "rlc_bandpass", "inverting_amp"]
)
def test_api_simulate_runs_every_circuit(address, circuit_id):
    status, content_type, body = post_api(address, defaults_body(circuit_id))
    assert status == 200
    assert content_type == JSON

    payload = json.loads(body)
    circuit = server.circuits.CIRCUITS[circuit_id]

    assert payload["analytic"]
    for item in circuit["checks"]:
        assert item["key"] in payload["analytic"]
        assert isinstance(payload[item["measured"]], float)

    headline = circuit["readout"]["headline"]["key"]
    assert isinstance(payload[headline], float)

    if circuit["analysis"] == "ac":
        assert len(payload["freq"]) == len(payload["mag_db"]) == len(payload["phase_deg"])


@requires_ngspice
def test_api_simulate_bandpass_resonates_where_predicted(address):
    _, _, body = post_api(address, defaults_body("rlc_bandpass"))
    payload = json.loads(body)
    assert payload["f0_measured"] == pytest.approx(5032.9, rel=0.02)
    assert payload["q_measured"] == pytest.approx(3.1623, rel=0.05)


@requires_ngspice
def test_api_simulate_amp_trades_gain_for_bandwidth(address):
    def bandwidth(rf):
        _, _, body = post_api(address, {
            "circuit": "inverting_amp",
            "params": {"rin": 1000, "rf": rf, "a0": 1e5, "gbw": 1e6},
        })
        return json.loads(body)

    modest = bandwidth(10000)
    greedy = bandwidth(100000)
    assert modest["f3db"] / greedy["f3db"] == pytest.approx(10.0, rel=0.1)


# ---- legacy endpoints stay byte-compatible ------------------------------


@requires_ngspice
def test_legacy_dc_response_has_exactly_the_old_shape(address):
    _, _, body = fetch(
        address, "/simulate", "POST", json.dumps({"vdd": 5, "r1": 10000, "r2": 10000})
    )
    payload = json.loads(body)
    assert list(payload) == ["vout", "analytic"]
    assert payload["vout"] == pytest.approx(2.5, abs=1e-9)
    assert payload["analytic"] == pytest.approx(2.5)


@requires_ngspice
def test_legacy_ac_response_has_exactly_the_old_shape_and_order(address):
    _, _, body = fetch(
        address, "/simulate_ac", "POST", json.dumps({"r": 1000, "c": 1.59e-7})
    )
    payload = json.loads(body)
    assert list(payload) == [
        "freq", "mag_db", "phase_deg", "f3db", "fc_analytic",
        "dc_gain_db", "phase_at_f3db",
    ]
    assert payload["f3db"] == pytest.approx(1000.9, rel=0.02)
    assert payload["fc_analytic"] == pytest.approx(1000.9745, rel=1e-4)


@requires_ngspice
def test_legacy_and_api_agree_on_the_same_circuit(address):
    _, _, legacy = fetch(
        address, "/simulate_ac", "POST", json.dumps({"r": 1000, "c": 1.59e-7})
    )
    _, _, modern = post_api(address, defaults_body("rc_lowpass"))

    old = json.loads(legacy)
    new = json.loads(modern)
    assert old["f3db"] == pytest.approx(new["f3db"], rel=1e-12)
    assert old["fc_analytic"] == pytest.approx(new["analytic"]["fc"], rel=1e-12)
    assert old["freq"] == new["freq"]


@requires_ngspice
def test_simulate_still_runs_end_to_end(address):
    status, content_type, body = fetch(
        address, "/simulate", "POST", json.dumps({"vdd": 5, "r1": 10000, "r2": 10000})
    )
    assert status == 200
    assert content_type == JSON
    payload = json.loads(body)
    assert payload["vout"] == pytest.approx(2.5, abs=1e-6)
    assert payload["analytic"] == pytest.approx(2.5, abs=1e-12)


def test_netlist_preview_returns_the_deck(address):
    body = json.dumps({"circuit": "divider",
                       "params": server.circuits.defaults("divider")})
    status, content_type, payload = fetch(address, "/api/netlist", "POST", body)
    assert status == 200
    netlist = json.loads(payload)["netlist"]
    assert "V1 in 0 DC 5" in netlist
    assert "print v(out)" in netlist


def test_netlist_preview_shows_placeholder_paths(address):
    body = json.dumps({"circuit": "rc_lowpass",
                       "params": server.circuits.defaults("rc_lowpass")})
    _, _, payload = fetch(address, "/api/netlist", "POST", body)
    netlist = json.loads(payload)["netlist"]
    # Placeholder names, never a real temp path.
    assert "response" in netlist
    assert "AppData" not in netlist


def test_netlist_preview_validates_like_simulate(address):
    body = json.dumps({"circuit": "divider", "params": {"vdd": 5}})
    status, _, payload = fetch(address, "/api/netlist", "POST", body)
    assert status == 400
    assert "'r1'" in json.loads(payload)["error"]
