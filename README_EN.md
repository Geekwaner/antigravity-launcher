# Antigravity Launcher

[![CI](https://github.com/Geekwaner/antigravity-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Geekwaner/antigravity-launcher/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/Geekwaner/antigravity-launcher?display_name=tag&sort=semver)](https://github.com/Geekwaner/antigravity-launcher/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-3ddc84.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-4c8dff.svg)](https://www.python.org/downloads/)

[简体中文](README.md) · [English](README_EN.md)

A **process-level proxy launcher** for the Google Antigravity desktop client. It does not take
over system networking; it writes proxy settings into Antigravity's process environment at the
moment it launches it.

- No TUN interface, no system proxy changes, no virtual NIC, no administrator rights
- Only affects the process tree it spawns; everything disappears when the client exits
- Pure Python 3.10+ standard library, no third-party dependencies (tkinter for the UI)

Screenshots:

| All three channels green | Leftover instances detected, launch blocked |
|---|---|
| ![ready](docs/preview-ready.png) | ![blocked](docs/preview-blocked.png) |

---

## 1. Why this exists

Antigravity is not a single process. The component that actually talks to the network is
`resources/bin/language_server.exe` (written in Go); OAuth, `loadCodeAssist`,
`fetchAvailableModels` and model streaming all go through it.

Three network stacks resolve proxies in incompatible ways:

| Layer | How it finds a proxy |
|---|---|
| Electron / Chromium | Reads the OS "system proxy" settings |
| Go `net/http` | **Environment variables only**, never the registry |
| Node core `https` | Reads neither |

So "my system proxy is on but Antigravity still cannot connect" is expected: the Go layer ignores
the registry, connects directly, and runs into DNS poisoning and TLS resets. Typical log lines are
`getaddrinfo ENOTFOUND daily-cloudcode-pa.googleapis.com` and
`Client network socket disconnected before secure TLS connection was established`.

Environment variables are only inherited when a process is **created**; they cannot be injected
into a running process. That is why this tool has to launch Antigravity itself, and why any old
instance must be fully closed first.

---

## 2. What it does

1. **Locate the client and the terminal CLI** — scans the usual install locations per platform
   (uses `mdfind` on macOS and skips its own wrapper directory, parses `.desktop` files on Linux),
   then confirms the match via the sibling `resources/bin/language_server` so a same-named binary
   is never mistaken for the client. It also detects the `agy` terminal CLI (common directories
   plus `PATH`). Manual override is supported and remembered.
2. **Validate the proxy (with auth support)** — reads the system proxy first (Windows registry /
   macOS `scutil` / Linux `gsettings`), then environment variables, then scans common local ports.
   Every candidate must complete a **real handshake**: both HTTP CONNECT and SOCKS5 (username and
   password supported), verified separately against `accounts.google.com` (login) and
   `daily-cloudcode-pa.googleapis.com` (model). After the tunnel is up, a TLS handshake must
   succeed through it. An open port alone does not count.
3. **Pre-launch Google reachability check (on by default, can be disabled)** — a local proxy
   handshake only proves the proxy client is listening, not that the remote node works. Before
   launch it can send one HTTPS request to `https://www.google.com/generate_204` through the same
   tunnel with **full certificate verification**, catching dead nodes and expired subscriptions.
   Toggle it in the bottom-right corner of the UI or skip it with `--no-preflight`.
4. **Live state tracking, own vs. leftover instances** — records the PID and real creation time of
   the client it launched (defends against Windows PID reuse), so a restarted launcher still
   recognizes its own instance and tracks its exit. Any external instance without injected proxy
   settings is shown in red and blocks launch, with a button to close it.
5. **Launch and terminal helpers** — injects `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` plus their
   lowercase variants, appends `--proxy-server` to pin Chromium to the same endpoint, and polls
   the process tree to confirm `language_server` took over (fails fast if the parent exits
   immediately). Also opens a proxy-injected terminal / `agy`, copies shell proxy commands, and
   runs a "direct IP vs proxy exit IP + latency" network diagnosis.

`NO_PROXY` always contains loopback (`localhost,127.0.0.1,::1`). This is not optional: the
Antigravity UI is served by a local Go service on `127.0.0.1` and the OAuth callback listens on
localhost, so proxying loopback results in a blank window.

---

## 3. Usage

### Option 1: Download a release (recommended for end users)

Grab a build from [GitHub Releases](https://github.com/Geekwaner/antigravity-launcher/releases) (no Python required):

- **`AntigravityLauncher-<version>-win-x64-portable.zip` (recommended)**: portable directory
  build, starts instantly and triggers fewer false positives. **Extract the whole archive first**,
  then run `AntigravityLauncher.exe` (a CLI tool `aglaunch.exe` ships alongside).
- **`AntigravityLauncher-<version>-win-x64-standalone.exe`**: single-file build, run directly
  (first start self-extracts, ~1–2 s).

> Releases currently provide **Windows x64 binaries only**. macOS / Linux users should run from
> source (Option 2).
>
> If SmartScreen shows "Windows protected your PC", click **More info → Run anyway**. You can
> verify checksums against `SHA256SUMS.txt` attached to the release.

### Option 2: Run from source

```
Double-click AntigravityLauncher.bat   (no console window, Python 3.10+)
```

Or from a terminal:

```bash
py -3 aglaunch.py
```

Detection runs automatically. Once all three channels are green, click the launch bar at the
bottom. For non-standard ports, enter `http://127.0.0.1:12345` or `socks5://127.0.0.1:12345` in
the "Proxy endpoint" field and press Enter to re-validate.

### Install as commands (optional)

```bash
py -3 -m pip install .
aglaunch --doctor        # CLI health check
antigravity-launcher     # GUI
```

Icon assets ship with the package, so the window icon works after installation.

### Build the `.exe` locally

```bash
py -3 -m pip install pyinstaller
py -3 scripts/build.py            # builds both the portable .zip and the standalone .exe into dist/
py -3 scripts/build.py --check-tag v1.0.0   # verify the tag matches __version__ before releasing
```

Pushing a `v*` tag triggers [release.yml](.github/workflows/release.yml), which verifies that the
tag matches the package version, runs the tests, builds both artifacts and publishes the release.

### Command line

```bash
# Read-only health check: no process killing, no client launch
py -3 aglaunch.py --doctor

# Verbose handshake log plus network diagnosis (direct vs proxy exit IP + latency)
py -3 aglaunch.py --doctor -v --diag

# Machine-readable
py -3 aglaunch.py --doctor --json

# Launch after a successful check (optionally clearing leftovers; skip the Google check with --no-preflight)
py -3 aglaunch.py --launch --kill-residuals

# Open a proxy-injected terminal and run the agy CLI (or just --terminal)
py -3 aglaunch.py --agy

# Manual endpoint
py -3 aglaunch.py --launch --proxy socks5://127.0.0.1:1080
```

Exit codes: `0` ready / `2` leftover instances / `3` proxy validation failed / `4` client not found.

---

## 4. Decision rules (key design)

- **Protocol classification**: `mixed` (both protocols pass), `http`, `socks5`, `dead`. A listening
  port, or a CONNECT that returns 200 but then breaks the TLS stream, does not count as usable.
- **Two targets verified separately**: login and model traffic may take completely different
  routes; one passing while the other fails is normal, so they are never merged into a single
  "is the proxy up" answer.
- **Retries**: each (protocol × target) combination retries twice by default and passes on any
  success. Cross-border routes are flaky enough that a single probe misjudges regularly.
- **Endpoint selection**: prefer an endpoint that passes both channels; otherwise fall back to the
  first protocol-usable endpoint and show its real state (the launch button becomes "Launch anyway
  (channels not all green)" and requires confirmation).
- **Mixed-port mapping**: for `mixed`, `HTTP_PROXY`/`HTTPS_PROXY` use `http://` and `ALL_PROXY`
  uses `socks5://`, so each layer gets what it handles best; `--proxy-server` uses `http://`
  (Chromium's CONNECT support is the most reliable).
- **Credentials**: authenticated proxies are passed through environment variables only, and are
  masked as `***` in the UI and logs. `--proxy-server` cannot carry credentials, so it is omitted
  with an explanatory log line.

---

## 5. Project layout

```
antigravity-launcher/
├── aglaunch.py              Entry point (GUI without args; CLI otherwise)
├── AntigravityLauncher.bat  Windows double-click launcher (no console, Python 3.10+)
├── pyproject.toml           Metadata and console entry points
├── CHANGELOG.md             Release history
├── LICENSE                  MIT
├── README_EN.md             This file
├── docs/                    UI screenshots
├── scripts/
│   ├── build.py             One-shot packaging (portable .zip + standalone .exe)
│   └── make_icon.py         Icon generator
├── tests/
│   └── test_launcher.py     Unit tests
├── .github/
│   ├── ISSUE_TEMPLATE/      Bug / feature request forms
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       ├── ci.yml           Three-platform test matrix
│       └── release.yml      Tag-triggered version check, build and release
└── aglauncher/
    ├── assets/              Application icons (shipped in the wheel)
    ├── client.py            Client discovery + language_server confirmation + agy detection
    ├── proxy.py             System proxy discovery, port scan, authenticated handshakes, diagnosis
    ├── procs.py             ctypes process enumeration / termination / tree identification
    ├── launcher.py          Env construction, masked preview, spawn, terminal helpers
    ├── core.py              Shared inspection pipeline (GUI and CLI)
    ├── gui.py               tkinter UI
    ├── cli.py               Command line
    └── store.py             Preferences persistence
```

Preferences live in `%APPDATA%\AntigravityLauncher\config.json` (client path, manual proxy, retry
settings only). Antigravity's own configuration is never touched and nothing is written to the
registry.

---

## 6. Security boundaries

- Only the spawned process tree is affected; everything disappears on exit.
- No registry writes, no system proxy changes, no virtual NIC, no TLS verification disabled.
- Closing processes asks for confirmation first and sends `WM_CLOSE` before force-terminating.
- Proxy credentials are masked in the UI and logs.
- Validation handshakes skip certificate-chain verification on purpose: the goal is to prove the
  tunnel carries real TLS traffic, and some proxies MITM with a locally issued root certificate,
  which would make working routes look dead. This does **not** affect Antigravity's own
  certificate verification — the tool never disables it.
- The only outbound data request happens when **network diagnosis is explicitly triggered** (the
  "Network diagnosis" button or `--doctor --diag`): it queries `http://ip-api.com/json` over plain
  HTTP for the direct exit IP, and the same endpoint through the proxy for the proxy exit IP. The
  request contains no local paths, accounts or Antigravity data, and it is never sent otherwise.
  No telemetry is uploaded anywhere.

---

## 7. Known limitations

- The UI is tkinter; at 200% display scaling controls grow with the system DPI and the window
  adapts between 880–915 px in height (capped at screen height minus 90 px).
- Only the process names `Antigravity.exe` and `language_server.exe` are recognized; upstream
  renames require updates here.
- Proxies configured through a PAC file cannot be read statically; the inspection notes call this out.
- If all channels are green and `language_server` is confirmed running, but Gemini web or the
  client still shows account errors / a blank page after browser OAuth, the cause is usually the
  Google account's risk-control or web session state rather than the local proxy. See the upstream
  community workaround [GEMINI-WEB-WORKAROUND](https://github.com/peroperoyui-lab/antigravity-proxy-launcher/blob/main/GEMINI-WEB-WORKAROUND.md)
  (not an official Google fix; judge the risk yourself before applying).

---

## 8. Relation to upstream projects

The process-level injection idea comes from
[peroperoyui-lab/antigravity-proxy-launcher](https://github.com/peroperoyui-lab/antigravity-proxy-launcher) (MIT);
the GUI reference implementation is
[Ikaros-521/antigravity-launcher](https://github.com/Ikaros-521/antigravity-launcher) (MIT, Electron).

This project is an independent Python implementation that contains no code from those projects,
with these deliberate trade-offs:

- tkinter instead of Electron: no Node runtime, zero bundled dependencies;
- "tunnel + real TLS handshake" two-stage validation with retries and endpoint comparison;
- post-launch process-tree verification that `language_server` is really a child of this launch;
- leftover instances are summarized, with PID details kept in the log to preserve layout space.

---

## 9. Contributing

- Before filing a bug, run `aglaunch --doctor -v --json` and attach the **redacted** output;
- Describe how you verified your change (`python -m unittest discover -s tests -v` should be green);
- See [CHANGELOG.md](CHANGELOG.md) for release history.

MIT licensed, see [LICENSE](LICENSE).
