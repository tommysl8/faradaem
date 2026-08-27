# Deploying Faradaem

Faradaem is a local-first tool: a standard-library Python server that shells
out to ngspice on the same machine. Deploying it means putting that whole
stack somewhere, not just the web pages.

## Run it locally (the normal way)

Once per machine:

```powershell
python install.py
```

That fetches ngspice and the SKY130 technology files into `~/.faradaem/tools`,
where the tool looks for them without any environment variable being set. Then,
every time:

```powershell
.\.venv\Scripts\Activate.ps1
python server.py
```

Then open http://127.0.0.1:8000. If anything is missing, `python doctor.py`
says what and prints the exact fix.

## Run it in Docker

```
docker build -t faradaem .
docker run -p 8000:8000 faradaem
```

The image installs ngspice from apt and bakes the SKY130 technology files in
at build time, using the same `install.py` that sets up a laptop, so no volume
needs mounting. That is 21 MB compressed, not the 2.2 GB a full PDK install
costs, because Faradaem needs only the technology files and the primitive
devices.

To use a PDK you already have on the host instead, mount it and name it:

```
docker run -p 8000:8000 -v /path/to/sky130/pdk:/pdk -e PDK_ROOT=/pdk faradaem
```

To enable the strategist inside the container, pass the keys:

```
docker run -p 8000:8000 \
  -e FARADAEM_OPENAI_KEY=... -e FARADAEM_ANTHROPIC_KEY=... faradaem
```

## Publishing the site at faradaem.com

### What can be published, and what cannot

The simulator cannot run on Vercel, and it is worth being exact about why
rather than discovering it halfway through a deploy:

- ngspice is a **native binary**. Vercel builds and runs your code; it does
  not let you `apt-get install` a simulator into the runtime. This is the
  reason that does not go away: the technology files Faradaem reads are 60 MB
  unpacked and would fit a function bundle's 250 MB fine, but nothing is going
  to run a simulation without a simulator.
- A PVT suite takes **minutes**. A Vercel function is capped at 10 s on the
  Hobby plan, 300 s at most on Pro.
- Design and robustness jobs live **in one process's memory**, polled over
  many requests. Serverless invocations do not share memory, so the next
  poll can land on a different machine that has never heard of the job.

So Vercel gets the half that is honestly static, and it is a real page, not
a placeholder: all four pages, the circuit catalogue, and **live schematics**,
which are drawn in the browser and redraw as you change values. The page
asks for `/api/circuits`, does not find a server, falls back to the
published `catalogue.json`, and puts away every panel that would need a
measured number. It says so in a note at the top. No number appears that
nothing measured.

### Deploy it

```
npm i -g vercel
vercel login
vercel --prod
```

Run from the repository root. `vercel.json` there carries the build command
(`python3 tools/build_static.py --out dist`) and the output directory, so
the same file serves the CLI and the Git integration; connecting the repo in
the Vercel dashboard needs no further settings and redeploys on every push.

If the build image has no Python, build locally and upload the result
instead:

```
python tools/build_static.py --out dist
vercel deploy --prod dist
```

### Point the domain at it

In the Vercel project, open Settings, then Domains, and add `faradaem.com`
and `www.faradaem.com`. Vercel prints the exact DNS records to create at the
registrar: an A record for the apex and a CNAME for `www`. Copy the values it
shows rather than any written here; they are Vercel's to change. Alternatively
move the domain's nameservers to Vercel and it manages both records itself.
DNS takes minutes to hours to propagate, and the certificate is issued
automatically once it has.

### Running the whole tool on the internet

If you want the simulator itself reachable, not just the pages, it needs a
host that runs a **container with a disk**, because it needs ngspice, the
technology files, and a process that stays alive between requests. Fly.io,
Render, Railway, or any VPS can do it; the `Dockerfile` here is the whole
description, and it bakes the technology files in, so nothing needs mounting.
Give it at least 2 CPUs, and read the warning below first, because it applies
with force to a public
host: with no authentication, anyone who finds the URL spends your API
credits and your CPU.

A reasonable arrangement is the static site on `faradaem.com` and the real
tool behind an authenticating tunnel on a subdomain, so the public page
stays free to serve and the expensive half stays yours.

## Environment variables

| Variable | Meaning | Default |
| --- | --- | --- |
| `FARADAEM_NGSPICE` | Full path to the ngspice binary | discovery: the copy under `~/.faradaem/tools`, then `ngspice_con.exe` on PATH, then `C:\ngspice\Spice64\bin\ngspice_con.exe` |
| `PDK_ROOT` | SKY130 PDK install root | discovery: the copy under `~/.faradaem/tools`, then `C:\pdk` |
| `FARADAEM_HOME` | Where installed tools and the ledger live | `~/.faradaem` |
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
