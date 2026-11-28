# Antigravity Launcher

[![CI](https://github.com/Geekwaner/antigravity-launcher/actions/workflows/ci.yml/badge.svg)](https://github.com/Geekwaner/antigravity-launcher/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/Geekwaner/antigravity-launcher?display_name=tag&sort=semver)](https://github.com/Geekwaner/antigravity-launcher/releases)
[![License: MIT](https://img.shields.io/badge/license-MIT-3ddc84.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-4c8dff.svg)](https://www.python.org/downloads/)

[简体中文](README.md) · [English](README_EN.md) | [快速开始](#快速开始) · [工作原理](#为什么需要它) · [命令行](#命令行用法) · [安全与隐私](#安全与隐私) · [常见问题](#常见问题与已知限制)

面向 **Google Antigravity 桌面客户端与 `agy` CLI** 的进程级代理启动器。不接管整机网络，只在拉起进程的那一刻将代理信息写入该进程树的环境变量，客户端退出即失效。

- **零侵入**：不开 TUN、不改系统代理、不装虚拟网卡、无需管理员权限
- **真握手校验**：不仅探测端口监听，还会穿过隧道分别完成登录端点与模型端点的 TLS 握手及 Google 可达性校验
- **进程树隔离与接管追踪**：自动区分己方拉起实例与未注入代理的残留实例，并在启动后确认 `language_server` 成功挂载
- **零第三方运行时依赖**：纯 Python 3.10+ 标准库实现（界面基于 `tkinter`），提供 Windows x64 与 macOS Apple Silicon 开箱即用发行包

| 三通道全绿，可启动 | 检测到未注入代理的残留实例，警示并阻断 |
|---|---|
| ![ready](docs/preview-ready.png) | ![blocked](docs/preview-blocked.png) |

---

## 快速开始

### 方式一：下载预编译版本（推荐，无需 Python）

前往 [GitHub Releases](https://github.com/Geekwaner/antigravity-launcher/releases) 下载对应系统的构建产物（每次推送 `v*` 标签由 GitHub Actions 自动构建）：

| 平台 | 文件 | 说明 |
|---|---|---|
| **Windows x64** | `AntigravityLauncher-<版本>-win-x64-standalone.exe` | 单文件便携版，免解压直接双击运行（首次打开需自解压约 1–2 秒） |
| **macOS Apple Silicon (M 系列)** | `AntigravityLauncher-<版本>-mac-arm64.dmg` | M1/M2/M3/M4 原生 `.app` 拖拽安装包（内置 `Contents/MacOS/aglaunch` CLI） |

> [!NOTE]
> - **其他平台**：macOS Intel 与 Linux 用户请使用下方「方式二：从源码运行」（仅需 Python 3.10+）。
> - **校验完整性**：所有产物均可在 Release 附件 `SHA256SUMS.txt` 中核对 SHA-256 校验值。
> - **Windows SmartScreen**：产物未购买商业代码签名证书；若弹出「已保护你的电脑」，点击 **更多信息 → 仍要运行**。
> - **macOS Gatekeeper**：`.app` 为本地 ad-hoc 签名未公证；拖入 `/Applications` 后首次打开若提示「已损坏」或「无法验证开发者」，在终端执行一次：
>   ```bash
>   xattr -cr /Applications/AntigravityLauncher.app
>   ```

### 方式二：从源码运行（全平台 / 开发者）

需要 **Python 3.10+**（含自带的 `tkinter`）：

```bash
git clone https://github.com/Geekwaner/antigravity-launcher.git
cd antigravity-launcher
```

- **Windows**：双击 `AntigravityLauncher.bat`（无控制台黑框启动界面），或在终端运行：
  ```powershell
  py -3 aglaunch.py
  ```
- **macOS / Linux**：
  ```bash
  python3 aglaunch.py
  ```

启动后会自动完成客户端定位与代理探测。三条通道均为绿色即可点击底部「启动 Antigravity（注入代理）」；如果代理使用非标准端口或需要认证，可在「代理端点」输入框填入 `http://127.0.0.1:12345` 或 `socks5://user:pass@127.0.0.1:12345` 后按回车重新校验。

### 方式三：安装为全局命令（可选）

```bash
py -3 -m pip install .
aglaunch --doctor        # 命令行体检
antigravity-launcher     # 启动图形界面
```

应用图标已内置于 `aglauncher/assets/`，通过 `pip` 安装后窗口图标同样正常显示。

---

## 为什么需要它

Antigravity 并非单一进程，真正负责对外通信的核心组件是 `resources/bin/language_server.exe`（Go 编写），OAuth 登录、`loadCodeAssist`、`fetchAvailableModels` 与模型流式响应全部由它发出。

客户端内部的三套网络栈读取代理的方式互不相通：

| 网络栈层级 | 代理读取方式 |
|---|---|
| Electron / Chromium | 读取操作系统的「系统代理」设置 |
| Go `net/http`（`language_server`） | **只读环境变量**，不读取 Windows 注册表系统代理 |
| Node 核心 `https` | 两者均不读取 |

因此「系统代理已经打开，但 Antigravity 依然连不上」是结构性原因：Go 层看不到系统代理而尝试直连，进而触发 DNS 污染或 TLS 连接重置（常见日志如 `getaddrinfo ENOTFOUND daily-cloudcode-pa.googleapis.com`、`Client network socket disconnected before secure TLS connection was established`）。

由于环境变量只能在**创建进程时**由子进程继承，无法事后注入已在运行的进程，因此必须由启动器先行清理未注入代理的旧实例，再以干净环境拉起客户端。

---

## 核心功能

1. **客户端与 `agy` CLI 自动定位**
   扫描 Windows / macOS / Linux 常见安装目录（macOS 兼容 `mdfind` 并排除包装目录，Linux 解析 `.desktop`），并通过同目录下的 `resources/bin/language_server` 进行二次确认，防止误认同名程序；同时自动识别 `agy` CLI（常见路径及 `PATH`）。也支持在界面或命令行手动指定并记住路径。
2. **三级代理发现与双协议认证握手**
   依次读取系统代理（Windows 注册表 / macOS `scutil` / Linux `gsettings`）、环境变量，并并发扫描常见本地代理端口。每个候选端点均支持用户名/密码认证，同时尝试 HTTP CONNECT 与 SOCKS5，分别对 `accounts.google.com`（登录）与 `daily-cloudcode-pa.googleapis.com`（模型）建立隧道并完成真实 TLS 握手。
3. **启动前 Google 真实可达性校验（默认开启）**
   本地代理握手成功只证明本地代理客户端在监听，不代表远端节点可用。启动前可选穿过同一隧道请求 `https://www.google.com/generate_204`（**保留完整证书校验**），提前拦截死节点或过期节点；可在界面右下角开关或通过 `--no-preflight` 跳过。
4. **实时状态追踪 & 己方/残留实例区分**
   记录由本启动器拉起的客户端 PID 与真实进程创建时间（防止 Windows PID 复用误判），重开启动器仍可识别自己拉起的实例并实时跟踪退出；若检测到外部未注入代理的残留进程，将显示警示条并阻断启动，同时提供「终止全部实例」按钮。
5. **终端工具与网络诊断**
   注入 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` 及小写变体，并附加 `--proxy-server` 锁定 Chromium 层；拉起后轮询进程树确认 `language_server` 接管（父进程秒退时立即告警）。此外支持一键打开代理终端 / 启动 `agy`、复制各 Shell（PowerShell / CMD / Bash / Git）代理命令，以及「直连 IP vs 代理出口 IP + 核心端点延迟」网络诊断。

> [!IMPORTANT]
> `NO_PROXY` 固定包含回环地址（`localhost,127.0.0.1,::1`）。Antigravity 界面由本地 Go 服务在 `127.0.0.1` 提供，OAuth 回调也监听在 `localhost`；若代理接管回环流量会导致客户端直接白屏或登录回调失败。

---

## 命令行用法

源码目录使用 `py -3 aglaunch.py`（macOS / Linux 用 `python3 aglaunch.py`）；若已通过 `pip install .` 安装，可直接把前缀替换为 `aglaunch`：

```bash
# 只读体检：不终止进程、不启动客户端
py -3 aglaunch.py --doctor

# 输出逐次握手明细 + 网络诊断（直连 vs 代理出口 IP 与延迟）
py -3 aglaunch.py --doctor -v --diag

# 输出 JSON（适合脚本集成或排查反馈）
py -3 aglaunch.py --doctor --json

# 体检通过后直接启动桌面客户端（自动清理残留实例；加 --no-preflight 可跳过 Google 校验）
py -3 aglaunch.py --launch --kill-residuals

# 打开已注入代理的终端并启动 agy CLI（仅打开终端可用 --terminal）
py -3 aglaunch.py --agy

# 手动指定代理端点（支持账号密码）
py -3 aglaunch.py --launch --proxy socks5://127.0.0.1:1080
```

| 退出码 | 含义 |
|---|---|
| `0` | 就绪 / 启动成功 |
| `1` | 运行异常或启动后校验失败 |
| `2` | 检测到未注入代理的残留实例 |
| `3` | 代理未通过握手或可达性校验 |
| `4` | 未找到客户端或 `agy` CLI |

---

## 判定规则与设计细节

- **协议判定**：分为 `mixed`（HTTP CONNECT 与 SOCKS5 均可用）/ `http` / `socks5` / `dead`。仅端口在监听，或 CONNECT 返回 `200` 但随后 TLS 握手断流，均不视为通过。
- **双通道独立验证**：登录端点与模型端点在代理规则中常走不同出站线路，一个通另一个不通很常见，因此绝不合并为单一状态。
- **抖动重试**：每个「协议 × 目标」组合默认最多重试 2 次，任一次成功即通过，减少跨境链路瞬时抖动导致的误判。
- **端点择优与降级提示**：优先选择双通道全绿的端点；若无全绿端点，则回退到首个协议可用的端点并如实标红未通通道（此时主按钮变为「仍要启动（有通道未通过）」，需二次确认）。
- **混合端口分流映射**：当端点为 `mixed` 时，`HTTP_PROXY` / `HTTPS_PROXY` 使用 `http://`，`ALL_PROXY` 使用 `socks5://`；`--proxy-server` 使用 `http://`（Chromium 对 HTTP CONNECT 兼容性最佳）。
- **凭证脱敏与参数约束**：带认证的代理仅通过环境变量传递，并在界面与日志中脱敏为 `***`；由于 Chromium 的 `--proxy-server` 参数不支持携带用户名密码，此时会自动省略该参数并在日志中提示。

---

## 安全与隐私

- 仅作用于本启动器拉起的进程树，不写入系统注册表、不修改系统代理、不安装虚拟网卡。
- 终止残留进程前必须经过二次确认，且优先发送 `WM_CLOSE`（POSIX 下为 `SIGTERM`）等待编辑器安全退出，超时后才强制结束。
- 代理用户名与密码在界面预览及日志中统一脱敏为 `***`。
- **关于探测阶段的 TLS 证书设置**：连通性握手阶段刻意不校验证书链，仅验证隧道能否承载真实 TLS 流量（避免开启本地 MITM/自签根证书调试代理时误判为死线）；这**完全不影响** Antigravity 客户端自身的证书校验。
- **对外网络请求说明**：除主动点击「网络诊断」或执行 `--diag` 时会请求明文 `http://ip-api.com/json` 查询直连与代理出口 IP 外（请求不含任何本地路径、账号或客户端数据），其余流程不会向任何第三方服务器上传数据或遥测。

---

## 常见问题与已知限制

<details>
<summary><strong>点击展开：常见问题排查（系统代理无效 / 启动被阻断 / Google 不可达 / OAuth 后白屏）</strong></summary>

1. **系统代理已开，为什么还是提示未发现可用代理？**
   请检查本地代理客户端是否允许局域网/本机回环连接、是否仅配置了浏览器 PAC 规则（PAC 脚本无法静态读取，界面「运行日志」会给出提示），或直接在「代理端点」手动填入真实监听端口（如 `http://127.0.0.1:7897`）。
2. **为什么启动按钮显示「已阻断：检测到 N 个未注入代理的残留实例」？**
   后台仍有未通过本启动器拉起的 `Antigravity` 或 `language_server` 进程，新进程若被单实例锁合并会继承不到代理。点击提示条右侧的「终止全部实例」即可安全清理并重新检测。
3. **三条通道全绿，但点击启动提示「Google 不可达」？**
   说明本地代理端口工作正常，但当前远端代理节点无法访问 `www.google.com/generate_204`（节点失效、过期或被阻断）。请切换代理节点后重试；如确认特殊内网/定制网关环境无需该校验，可取消勾选右下角「Google 校验」或使用 `--no-preflight`。
4. **所有通道全绿且 `language_server` 已接管，但浏览器 OAuth 后仍出现账号异常或白屏？**
   这类问题通常源于 Google 账号风控或 Web 会话状态，与本地代理注入无关，可参考上游社区排查记录 [GEMINI-WEB-WORKAROUND](https://github.com/peroperoyui-lab/antigravity-proxy-launcher/blob/main/GEMINI-WEB-WORKAROUND.md)（非官方方案，请自行评估风险）。

</details>

- **DPI 与窗口尺寸**：基于 `tkinter`，默认窗口尺寸 `980×880`（最小 `920×780`），出现残留实例警示条时会自动向下扩展高度（上限为屏幕高度减 90px）。
- **进程名识别**：桌面端识别 `Antigravity` / `Antigravity.exe` 及 `language_server*`（兼容带架构后缀的网关可执行文件）；若官方变更主程序名称需同步调整。
- **PAC 限制**：暂不支持解析 PAC 自动配置脚本中的代理规则。

---

## 目录结构与本地构建

```text
antigravity-launcher/
├── aglaunch.py              统一入口（无参数开 GUI，带参数走 CLI）
├── AntigravityLauncher.bat  Windows 双击无黑框启动脚本（Python 3.10+）
├── pyproject.toml           项目元数据、包资源与命令入口配置
├── CHANGELOG.md             版本变更记录
├── LICENSE                  MIT 开源协议
├── README_EN.md             English README
├── docs/                    文档截图
├── scripts/
│   ├── build.py             PyInstaller 打包脚本（Windows standalone .exe / macOS arm64 .dmg）
│   └── make_icon.py         应用图标生成脚本
├── tests/
│   └── test_launcher.py     单元测试
├── .github/
│   ├── ISSUE_TEMPLATE/      Bug / 功能请求模板
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       ├── ci.yml           Windows / macOS / Ubuntu 三平台测试矩阵
│       └── release.yml      v* 标签触发版本校验、测试、打包与发布
└── aglauncher/
    ├── assets/              内置图标资源（随包分发）
    ├── client.py            客户端定位、language_server 确认与 agy 检测
    ├── proxy.py             系统代理读取、端口扫描、认证握手与网络诊断
    ├── procs.py             ctypes 进程枚举 / 优雅退出 / 进程树追踪
    ├── launcher.py          环境变量构建、脱敏预览、进程拉起与终端助手
    ├── core.py              共享探测流水线（GUI 与 CLI 共用）
    ├── gui.py               tkinter 图形界面
    ├── cli.py               命令行接口
    └── store.py             本地偏好持久化
```

偏好配置文件路径：
- **Windows**：`%APPDATA%\AntigravityLauncher\config.json`
- **macOS / Linux**：`~/.config/AntigravityLauncher/config.json`

仅保存启动器自身的客户端路径、手动代理、校验参数与已拉起 PID 记录，不修改 Antigravity 自身配置。

### 本地构建当前平台发行包

```bash
python -m pip install pyinstaller
python scripts/build.py                      # Windows: standalone.exe | macOS: .dmg
python scripts/build.py --check-tag v1.1.1   # 发版前校验 tag 与 __version__ 是否一致
```

推送 `v*` 标签会自动触发 [release.yml](.github/workflows/release.yml) 在 GitHub Actions 中完成版本校验、测试、Windows x64 单文件版与 macOS Apple Silicon `.dmg` 构建与 Release 发布；也支持在 GitHub Actions 页面手动点击 **Run workflow** 进行不发布的试打包验证。

---

## 致谢与上游项目

进程级代理注入的核心思路来自 [peroperoyui-lab/antigravity-proxy-launcher](https://github.com/peroperoyui-lab/antigravity-proxy-launcher)（MIT），图形界面的功能布局参考了 [Ikaros-521/antigravity-launcher](https://github.com/Ikaros-521/antigravity-launcher)（MIT，Electron）。

本项目为独立的 Python 实现，不包含上述项目的源码，并在工程实现上做了以下取舍：

- 使用 Python 标准库 + `tkinter` 替代 Electron，无需 Node 运行时、零第三方运行时依赖；
- 采用「隧道建立 + 真实 TLS 握手 + 重试比选 + 启动前 Google 可达性校验」多段验证；
- 启动后通过进程树血缘追踪确认 `language_server` 确实由本次启动的父进程拉起；
- 区分己方拉起实例与外部残留实例，并用进程创建时间防止 Windows PID 复用误判。

---

## 参与贡献

- 提交 Bug 前请先运行 `py -3 aglaunch.py --doctor -v --json`（或 `aglaunch --doctor -v --json`），并在 Issue 中附上**脱敏后**的输出结果；
- 提交 PR 前请确保单元测试全部通过：`py -3 -m unittest discover -s tests -v`；
- 版本变更历史见 [CHANGELOG.md](CHANGELOG.md)。

本项目基于 [MIT License](LICENSE) 开源。
