# Deploying Faradaem

Faradaem is a local-first tool: a standard-library Python server that shells
out to ngspice on the same machine. Deploying it means putting that whole
stack somewhere, not just the web pages.

## Run it locally (the normal way)

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Then open http://127.0.0.1:8000.

## Run it in Docker

```
docker build -t faradaem .
docker run -p 8000:8000 -v /path/to/sky130/pdk:/pdk faradaem
```

The image installs ngspice from apt and expects the SKY130 PDK mounted at
`/pdk` (the directory that contains `sky130A/`). Without the mount, the three
SKY130 circuits report what to install and every other circuit works.

To enable the strategist inside the container, pass the keys:

```
docker run -p 8000:8000 -v /path/to/pdk:/pdk \
  -e FARADAEM_OPENAI_KEY=... -e FARADAEM_ANTHROPIC_KEY=... faradaem
```

## Environment variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `FARADAEM_NGSPICE` | Full path to the ngspice binary | discovery: `ngspice_con.exe` on PATH, then `C:\ngspice\Spice64\bin\ngspice_con.exe` |
| `PDK_ROOT` | SKY130 PDK install root | `C:\pdk` |
| `FARADAEM_HOST` | Bind address | `127.0.0.1` |
| `FARADAEM_PORT` | Port | `8000` |
| `FARADAEM_ANTHROPIC_KEY` | Anthropic API key for the strategist | unset |
| `FARADAEM_OPENAI_KEY` | OpenAI API key for the strategist | unset |
| `FARADAEM_ANTHROPIC_MODEL` | Model override | `claude-sonnet-5` |
| `FARADAEM_OPENAI_MODEL` | Model override | `gpt-5.6-terra` |

On Windows, keys set with `setx` are found even by a server that was started
earlier: the app reads the user-scope registry as a fallback.

## Before exposing it publicly, read this

The app ships with **no authentication**. Three consequences:

- Anyone who can reach it can run simulations on your CPU, including
  multi-minute PVT suites.
- Anyone who can reach it can drive the strategist, which **spends your API
  credits**.
- The design and advise endpoints hold jobs in memory with modest caps, not
  hardened rate limiting.

If you put it on the public internet, front it with a reverse proxy that adds
authentication (Caddy or nginx with basic auth is enough for a demo), or bind
it to a private network only. For a personal demo, a tunnel with built-in
auth (Tailscale Funnel, Cloudflare Access) is the sane route.

## Continuous integration

`.github/workflows/ci.yml` runs the full suite on every push with a real
ngspice from apt. The SKY130 PDK and the LLM keys are deliberately absent
there; the tests that need them skip cleanly, and that skip behaviour is part
of what the suite verifies.
