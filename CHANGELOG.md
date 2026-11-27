# 更新日志 / Changelog

本项目的所有重要变更都记录在这里。格式参考
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.1.0] - 2026-11-27

新增预编译发行包，发布流程现在会在同一个 Release 中提供两个平台的安装包。

### 新增

- macOS Apple Silicon（M 系列）原生 `.dmg` 安装包，内置 `.app` 与 `aglaunch` 命令行入口。
- 发布流程支持 Windows 与 macOS 双平台并行构建，并生成统一的 `SHA256SUMS.txt`。

### 变更

- Windows 发行包调整为单文件版 `.exe`；目录版 `.zip` 不再随 Release 发布。
- Linux 桌面项扫描会跳过启动器自身的包装 `.desktop` 文件，避免误认客户端。

### 修复

- 发布工作流增加手动触发入口，便于在不发布的情况下验证打包。

## [1.0.0] - 2026-11-26

首次开源发布。

### 新增

- 进程级代理注入：只给被拉起的那一棵进程树写入 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY`
  及小写变体，并通过 `--proxy-server` 固定 Chromium 层；不改系统代理、不开 TUN。
- 客户端定位：Windows / macOS / Linux 常见安装目录扫描，配合 `resources/bin/language_server`
  二次确认；支持手动指定并记住路径。
- 代理校验：系统代理（注册表 / `scutil` / `gsettings`）、环境变量、常见本地端口三级发现；
  HTTP CONNECT 与 SOCKS5 双协议真实握手，支持用户名/密码认证；登录端点与模型端点分别验证。
- 启动前 Google 真实可达性校验（`www.google.com/generate_204`，保留完整证书验证），
  可在界面开关或使用 `--no-preflight` 跳过。
- 残留实例识别：区分本启动器拉起的实例与未注入代理的外部实例，亮红阻断并提供终止按钮；
  使用进程创建时间防御 Windows PID 复用。
- 启动后进程树验证：确认 `language_server` 已被本次拉起的父进程带起，父进程秒退时快速告警。
- 终端工具：一键打开注入代理的终端、启动 `agy` CLI、复制 PowerShell / CMD / Bash / Git
  代理命令。
- 网络诊断：直连出口 IP 与代理出口 IP 对比、核心端点延迟测量。
- 图形界面（tkinter）与命令行（`--doctor` / `--launch` / `--terminal` / `--agy` /
  `--json` / `--diag`）共用同一条探测流水线。
- 打包脚本：一次构建目录绿色版 `.zip` 与单文件版 `.exe`，输出 `SHA256SUMS.txt`。
- CI：Ubuntu / Windows / macOS × Python 3.10 / 3.12 编译检查与单元测试。
- Release 工作流：推送 `v*` 标签时校验 tag 与 `__version__` 一致后自动打包发布。

### 安全

- 代理凭证在界面与日志中脱敏为 `***`。
- 终止进程前二次确认，优先发送 `WM_CLOSE`。
- 除主动触发的网络诊断外，不发起任何对外数据请求。

[Unreleased]: https://github.com/Geekwaner/antigravity-launcher/compare/v1.1.0...HEAD
[1.1.0]: https://github.com/Geekwaner/antigravity-launcher/releases/tag/v1.1.0
[1.0.0]: https://github.com/Geekwaner/antigravity-launcher/releases/tag/v1.0.0
