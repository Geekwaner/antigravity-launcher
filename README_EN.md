# Antigravity Launcher

[![CI](https://github.com/Geekwaner/antigravity-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Geekwaner/antigravity-launcher/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/Geekwaner/antigravity-launcher?display_name=tag&sort=semver)](https://github.com/Geekwaner/antigravity-launcher/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-3ddc84.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-4c8dff.svg)](https://www.python.org/downloads/)

[简体中文](README.md) · [English](README_EN.md) | [Quick Start](#quick-start) · [Why This Exists](#why-this-exists) · [CLI Usage](#command-line-usage) · [Security & Privacy](#security--privacy) · [FAQ & Limitations](#faq--known-limitations)

A **process-level proxy launcher** for the **Google Antigravity desktop client and `agy` CLI**. Instead of taking over system-wide networking, it injects proxy settings solely into the spawned process tree at launch time—leaving everything clean once the client exits.

- **Zero system intrusion**: No TUN interface, no system proxy changes, no virtual NIC, no administrator privileges
- **Real handshake validation**: Probes go beyond open ports—validating both HTTP CONNECT and SOCKS5 through live TLS handshakes against login and model endpoints, plus optional Google reachability preflight
- **Process-tree isolation & tracking**: Distinguishes its own launched client from leftover un-proxied instances and verifies `language_server` attachment after launch
- **Zero runtime dependencies**: Built purely on Python 3.10+ standard library (`tkinter` UI), with prebuilt packages for Windows x64 and macOS Apple Silicon

| All three channels green, ready to launch | Leftover instances detected, warned and blocked |
|---|---|
| ![ready](docs/preview-ready.png) | ![blocked](docs/preview-blocked.png) |

---

## Quick Start

### Option 1: Download Prebuilt Packages (Recommended, No Python Required)

Download the build for your platform from [GitHub Releases](https://github.com/Geekwaner/antigravity-launcher/releases) (automatically built via GitHub Actions on every `v*` tag):

| Platform | Artifact | Notes |
|---|---|---|
| **Windows x64** | `AntigravityLauncher-<version>-win-x64-standalone.exe` | Single-file executable; run directly without extracting (first launch self-extracts in ~1–2 s) |
| **macOS Apple Silicon (M-series)** | `AntigravityLauncher-<version>-mac-arm64.dmg` | Native Apple Silicon `.app` drag-to-install image (includes `Contents/MacOS/aglaunch` CLI) |

> [!NOTE]
> - **Other platforms**: macOS Intel and Linux users can run directly from source (Option 2, requires Python 3.10+).
> - **Checksum verification**: Verify downloaded files against `SHA256SUMS.txt` attached to each release.
> - **Windows SmartScreen**: Binaries are unsigned; if SmartScreen shows "Windows protected your PC", click **More info → Run anyway**.
> - **macOS Gatekeeper**: `.app` bundles are ad-hoc signed (not notarized). After dragging to `/Applications`, if macOS blocks first launch, run once in Terminal:
>   ```bash
>   xattr -cr /Applications/AntigravityLauncher.app
>   ```

### Option 2: Run from Source (All Platforms / Developers)

Requires **Python 3.10+** (with standard `tkinter`):

```bash
git clone https://github.com/Geekwaner/antigravity-launcher.git
cd antigravity-launcher
```

- **Windows**: Double-click `AntigravityLauncher.bat` (launches GUI with no console window), or run:
  ```powershell
  py -3 aglaunch.py
  ```
- **macOS / Linux**:
  ```bash
  python3 aglaunch.py
  ```

Client discovery and proxy probing run automatically on startup. Once all three channels turn green, click **Launch** at the bottom. For non-standard ports or authenticated proxies, enter `http://127.0.0.1:12345` or `socks5://user:pass@127.0.0.1:12345` in the **Proxy endpoint** field and press Enter.

### Option 3: Install as Global Commands (Optional)

```bash
py -3 -m pip install .
aglaunch --doctor        # CLI health check
antigravity-launcher     # Launch GUI
```

Icon assets are packaged inside `aglauncher/assets/`, so window icons display properly after installation.

---

## Why This Exists

Antigravity is a multi-process application. Actual outbound network requests—OAuth, `loadCodeAssist`, `fetchAvailableModels`, and model streaming—are handled by `resources/bin/language_server.exe` (written in Go).

Its three internal network stacks resolve proxy settings differently:

| Network Stack Layer | Proxy Resolution Behavior |
|---|---|
| Electron / Chromium | Reads OS "system proxy" settings |
| Go `net/http` (`language_server`) | **Environment variables only**; ignores Windows registry proxy settings |
| Node core `https` | Reads neither |

That is why *"my system proxy is enabled, yet Antigravity still fails to connect"* happens reliably: the Go layer bypasses the system proxy, connects directly, and hits DNS poisoning or TLS resets (commonly surfaced as `getaddrinfo ENOTFOUND daily-cloudcode-pa.googleapis.com` or `Client network socket disconnected before secure TLS connection was established`).

Because environment variables are inherited only at **process creation** and cannot be injected into an already-running process, this launcher ensures any un-proxied leftover instances are closed first and then spawns Antigravity with a clean proxy environment.

---

## Core Features

1. **Client & `agy` CLI Discovery**
   Scans standard installation paths on Windows, macOS (`mdfind` with wrapper exclusion), and Linux (`.desktop` parsing), and verifies each candidate against sibling `resources/bin/language_server` so unrelated binaries with similar names are never misidentified. Also detects the `agy` terminal CLI (standard paths and `PATH`), with persistent manual overrides.
2. **Three-Tier Proxy Discovery & Authenticated Dual-Protocol Handshake**
   Reads OS system proxy settings (Windows registry / macOS `scutil` / Linux `gsettings`), inspects environment variables, and concurrently scans common local proxy ports. Every candidate (including username/password auth) is tested with both HTTP CONNECT and SOCKS5 by establishing a tunnel and completing a real TLS handshake separately against `accounts.google.com` (login) and `daily-cloudcode-pa.googleapis.com` (model).
3. **Pre-Launch Google Reachability Check (Enabled by Default)**
   A local handshake only confirms the local proxy client is listening—not that the upstream node works. Before launch, the tool optionally sends an HTTPS request to `https://www.google.com/generate_204` through the validated tunnel with **full certificate verification**, catching dead or expired nodes early. Toggle it in the bottom-right corner or pass `--no-preflight`.
4. **Live Process Tracking & Own vs. Leftover Separation**
   Records the spawned client PID and exact process creation timestamp (protecting against Windows PID reuse), allowing a restarted launcher to recognize its own running instance. External un-proxied instances trigger a warning banner, block launch, and can be closed with one click.
5. **Terminal Helpers & Network Diagnostics**
   Injects `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` (and lowercase variants), appends `--proxy-server` for Chromium, and polls the process tree to verify `language_server` ownership (failing fast on immediate parent exits). Also provides one-click proxy-injected terminals / `agy`, copyable shell commands (PowerShell / CMD / Bash / Git), and direct vs. proxy exit IP + latency diagnostics.

> [!IMPORTANT]
> `NO_PROXY` always includes loopback addresses (`localhost,127.0.0.1,::1`). Antigravity renders its UI from a local Go server on `127.0.0.1` and receives OAuth callbacks on `localhost`; proxying loopback traffic causes a blank window or broken sign-in flow.

---

## Command-Line Usage

From source, run with `py -3 aglaunch.py` (or `python3 aglaunch.py` on macOS/Linux). If installed via `pip`, replace the prefix with `aglaunch`:

```bash
# Read-only health check: no process killing, no client launch
py -3 aglaunch.py --doctor

# Verbose per-attempt handshake log + network diagnostics (direct vs proxy exit IP + latency)
py -3 aglaunch.py --doctor -v --diag

# Machine-readable JSON output
py -3 aglaunch.py --doctor --json

# Launch desktop client after checks pass (auto-close leftovers; add --no-preflight to skip Google check)
py -3 aglaunch.py --launch --kill-residuals

# Open a proxy-injected terminal and launch agy CLI (or --terminal only)
py -3 aglaunch.py --agy

# Manual proxy endpoint (auth supported)
py -3 aglaunch.py --launch --proxy socks5://127.0.0.1:1080
```

| Exit Code | Meaning |
|---|---|
| `0` | Ready / launch succeeded |
| `1` | Runtime error or post-launch verification failed |
| `2` | Leftover un-proxied instances detected |
| `3` | Proxy handshake or reachability check failed |
| `4` | Antigravity client or `agy` CLI not found |

---

## Decision Rules & Design Details

- **Protocol classification**: `mixed` (both HTTP CONNECT and SOCKS5 succeed), `http`, `socks5`, or `dead`. An open port—or a `200` CONNECT response followed by a broken TLS handshake—does not pass.
- **Independent channel verification**: Login and model endpoints often route through different outbound rules; one passing while the other fails is common, so they are never collapsed into a single status.
- **Retry on transient jitter**: Each `(protocol × target)` pair retries up to 2 times by default and passes on the first success, avoiding false negatives on flaky routes.
- **Endpoint selection & fallback**: Prefers an endpoint green on both channels; otherwise falls back to the first protocol-usable endpoint and displays the exact channel failures (requiring explicit confirmation before launching).
- **Mixed-port mapping**: For `mixed` endpoints, `HTTP_PROXY`/`HTTPS_PROXY` use `http://` and `ALL_PROXY` uses `socks5://`; Chromium receives `--proxy-server=http://...` (best CONNECT compatibility).
- **Credential handling**: Proxy credentials are passed via environment variables only and masked as `***` in UI previews and logs. Since Chromium's `--proxy-server` flag cannot carry credentials, it is omitted with a log warning when auth is present.

---

## Security & Privacy

- Scoped strictly to the spawned process tree; no registry edits, no system proxy changes, no virtual NICs.
- Terminating leftover processes requires confirmation and sends `WM_CLOSE` (`SIGTERM` on POSIX) before force-killing.
- Proxy credentials are masked as `***` across UI previews and logs.
- **About validation-stage TLS settings**: Probe handshakes skip certificate-chain verification intentionally to verify that the tunnel carries TLS traffic (preventing false failures behind local MITM/debugging proxies with custom root CAs). This **never** disables Antigravity's own TLS certificate verification.
- **Outbound network requests**: The only external request made by the tool itself occurs when you explicitly trigger **Network diagnosis** (UI button or `--diag`), which queries `http://ip-api.com/json` over plain HTTP to compare direct vs. proxy exit IPs (containing no local paths, accounts, or Antigravity data). No telemetry is sent anywhere.

---

## FAQ & Known Limitations

<details>
<summary><strong>Click to expand: Troubleshooting (System proxy ignored / Blocked launch / Google unreachable / Post-OAuth blank page)</strong></summary>

1. **My system proxy is enabled—why does the launcher find no usable proxy?**
   Check that your local proxy client accepts loopback connections, verify you are not relying solely on a PAC script (PAC rules cannot be read statically; check the runtime log note), or enter your proxy port manually in **Proxy endpoint** (e.g. `http://127.0.0.1:7897`).
2. **Why is the launch button blocked due to leftover instances?**
   Existing `Antigravity` or `language_server` processes started outside the launcher lack proxy environment variables; single-instance locking would prevent a fresh launch from taking effect. Click **Terminate all instances** in the warning bar to clean them up safely.
3. **All three channels are green, why does preflight say Google is unreachable?**
   Your local proxy port is reachable, but the upstream proxy node failed to reach `www.google.com/generate_204` (dead or expired node). Switch proxy nodes and retry—or uncheck **Google check** in the bottom-right corner (CLI: `--no-preflight`) if your custom gateway intentionally blocks it.
4. **All channels are green and `language_server` is attached, yet browser OAuth still ends in an account error or blank screen?**
   This is typically caused by Google account risk-control or web session state rather than local proxying. See the upstream community workaround [GEMINI-WEB-WORKAROUND](https://github.com/peroperoyui-lab/antigravity-proxy-launcher/blob/main/GEMINI-WEB-WORKAROUND.md) (unofficial; evaluate risk yourself before applying).

</details>

- **DPI & window geometry**: Built with `tkinter`, defaulting to `980×880` (`920×780` minimum) and expanding vertically if the leftover-instance warning appears (capped at screen height minus 90 px).
- **Process names**: Matches `Antigravity` / `Antigravity.exe` and `language_server*` (including architecture-suffixed variants); upstream main binary renames require corresponding updates.
- **PAC limitation**: Proxy rules inside PAC auto-config scripts cannot be evaluated statically.

---

## Project Layout & Local Packaging

```text
antigravity-launcher/
├── aglaunch.py              Entry point (GUI by default; CLI with flags)
├── AntigravityLauncher.bat  Windows console-free launcher (Python 3.10+)
├── pyproject.toml           Package metadata, bundled assets, entry points
├── CHANGELOG.md             Release history
├── LICENSE                  MIT License
├── README_EN.md             English README
├── docs/                    UI screenshots
├── scripts/
│   ├── build.py             PyInstaller bundler (Windows standalone .exe / macOS arm64 .dmg)
│   └── make_icon.py         Application icon generator
├── tests/
│   └── test_launcher.py     Unit tests
├── .github/
│   ├── ISSUE_TEMPLATE/      Bug / feature request templates
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       ├── ci.yml           Windows / macOS / Ubuntu test matrix
│       └── release.yml      Tag-triggered version check, build, and release
└── aglauncher/
    ├── assets/              Bundled application icons
    ├── client.py            Client discovery, language_server verification, agy detection
    ├── proxy.py             System proxy reading, port scan, auth handshakes, diagnostics
    ├── procs.py             ctypes process enumeration, graceful close, tree tracking
    ├── launcher.py          Env builder, masked preview, process spawn, terminal helpers
    ├── core.py              Shared inspection pipeline (GUI & CLI)
    ├── gui.py               tkinter UI
    ├── cli.py               CLI interface
    └── store.py             Local preferences persistence
```

Preferences file location:
- **Windows**: `%APPDATA%\AntigravityLauncher\config.json`
- **macOS / Linux**: `~/.config/AntigravityLauncher/config.json`

Only stores launcher-specific settings (client path, manual proxy, probe parameters, and last launched PID record); Antigravity's own configuration is never modified.

### Building Release Packages Locally

```bash
python -m pip install pyinstaller
python scripts/build.py                      # Windows: standalone.exe | macOS: .dmg
python scripts/build.py --check-tag v1.1.1   # verify tag matches __version__ before releasing
```

Pushing a `v*` tag triggers [release.yml](.github/workflows/release.yml) in GitHub Actions to validate the version tag, run tests, build the Windows x64 standalone `.exe` and macOS Apple Silicon `.dmg`, and publish the GitHub Release. You can also trigger **Run workflow** manually in GitHub Actions to dry-run the build without publishing.

---

## Credits & Upstream Inspiration

The process-level proxy injection approach was inspired by [peroperoyui-lab/antigravity-proxy-launcher](https://github.com/peroperoyui-lab/antigravity-proxy-launcher) (MIT), and the UI layout takes inspiration from [Ikaros-521/antigravity-launcher](https://github.com/Ikaros-521/antigravity-launcher) (MIT, Electron).

This is an independent Python implementation containing no source code from either project, with deliberate architectural choices:

- Pure Python stdlib + `tkinter` instead of Electron (no Node runtime, zero runtime dependencies);
- Multi-stage validation: tunnel establishment, live TLS handshakes, retry/fallback selection, and pre-launch Google reachability verification;
- Post-launch process-tree ancestry verification confirming `language_server` belongs to the newly spawned parent;
- Separation of launcher-owned vs. leftover instances using process creation timestamps to prevent Windows PID reuse collisions.

---

## Contributing

- Before reporting a bug, please run `py -3 aglaunch.py --doctor -v --json` (or `aglaunch --doctor -v --json`) and attach the **redacted** output in your issue;
- Before opening a PR, ensure all unit tests pass: `py -3 -m unittest discover -s tests -v`;
- See [CHANGELOG.md](CHANGELOG.md) for release history.

Released under the [MIT License](LICENSE).
