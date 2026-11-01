<div align="center"><a name="readme-top"></a>

# Uninstall Tencent Service

A Bash script that uninstalls the Tencent Cloud Host Security (YunJing) agent and cleans up its remnants,<br/>
optionally removing Cloud Monitor (BaradAgent / Sgagent) and TencentCloud Automation Tools (TAT) as well.

[简体中文](./README.md) · [Report Issues][github-issues-link] · [Documentation][doc-yunjing-link]

<!-- SHIELD GROUP -->

[![][github-stars-shield]][github-stars-link]
[![][github-forks-shield]][github-forks-link]
[![][github-issues-shield]][github-issues-link]
[![][github-license-shield]][github-license-link]<br/>
[![][github-contributors-shield]][github-contributors-link]
[![][github-lastcommit-shield]][github-lastcommit-link]
[![][github-ci-shield]][github-ci-link]

</div>

<details>
<summary><kbd>Table of Contents</kbd></summary>

#### TOC

- [📋 Overview](#-overview)
- [⚠️ Prerequisites](#️-prerequisites)
- [🚀 Quick Start](#-quick-start)
- [📖 Usage](#-usage)
- [🔩 How It Works](#-how-it-works)
- [🛠 Troubleshooting](#-troubleshooting)
- [📚 References](#-references)
- [📝 License](#-license)

####

<br/>

</details>

## 📋 Overview

Tencent Cloud servers can have Host Security, Cloud Monitor and Automation Tools installed, with cron jobs and services keeping their agents running. This script first runs the official uninstallers already present on the server, then stops, removes and checks the known remnants of the selected components.

- 🛡️ Removes the Host Security agent (formerly YunJing, `YDService` / `YDLive`) by default
- 📊 Add `--include-monitor` to also remove Cloud Monitor (`barad_agent` / `sgagent`)
- 🤖 Add `--include-tat` to also remove Automation Tools (`tat_agent`)
- 🧹 Cleans up cron jobs, service units, runtime directories, installer packages, PID files and cgroups left by the official uninstallers
- 🔍 Verifies known files, processes, services and cron jobs after removal, with a short observation window for agent reappearance
- 🌐 Fetches no additional uninstallers; prints Chinese by default, `--lang en` switches to English. Official vendor programs may contact Tencent Cloud

The script only touches the known paths of these three components. Other image initialization files under `/usr/local/qcloud` stay untouched, and the script never changes console settings or reboots the system.

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## ⚠️ Prerequisites

1. Linux with `bash` installed
2. Run as `root` or an account with equivalent `sudo` privileges
3. `awk`, `readlink`, `stat`, `grep`, `cksum`, `find`, `timeout`, `flock`, `mkfifo` and `stdbuf` are installed, and Linux `/proc` is readable

The script checks dependencies but never installs software. When `timeout` lacks `-k` (as on CentOS 6), commands that hit the deadline receive SIGKILL directly and the script prints a notice. `--force` also needs `lsattr` and `chattr` to repair file attributes that block removal.

> \[!IMPORTANT]
>
> **Agent uninstall protection** does not need to be turned off before running this script. After you confirm the plan, or pass `-y`, the script automatically enters the 6-digit code displayed by the official uninstaller. Both protection-enabled and protection-disabled runs have been verified on real servers.
>
> If official removal still cannot complete, the agent remains running, or kernel protection remains, follow the reported failure before retrying. The script does not treat a zero exit from the official program alone as proof of removal. See [Troubleshooting](#-troubleshooting).
>
> After a successful uninstall, the console raises a client uninstall alert. This is expected.

### Supported Systems

| Category | Coverage |
| --- | --- |
| Distributions | TencentOS Server, OpenCloudOS, CentOS, CentOS Stream, Rocky Linux, AlmaLinux, Fedora, openSUSE Leap, Debian, Ubuntu, CoreOS |
| Init systems | systemd, SysVinit (`chkconfig` / `update-rc.d`), OpenRC (`rc-update`), Upstart |
| Install paths | Standard `/usr/local/qcloud/...`, and `/var/lib/qcloud/...` used when `/usr` is read-only (such as CoreOS) |
| CPU architectures | x86_64, aarch64 |
| Bash | 4.1 or later |
| coreutils | GNU coreutils, and the uutils coreutils shipped by default on Ubuntu 26.04 |

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🚀 Quick Start

Connect over SSH, download the script, and preview the default Host Security removal:

```bash
curl -fsSLo UTS.sh https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh &&
  sudo bash UTS.sh --lang en --dry-run
```

Review the plan, then run it. The script asks for confirmation before uninstalling:

```bash
sudo bash UTS.sh --lang en
```

Also remove Cloud Monitor and Automation Tools:

```bash
sudo bash UTS.sh --lang en --dry-run --include-monitor --include-tat
sudo bash UTS.sh --lang en --include-monitor --include-tat
```

Unattended run, skipping confirmation:

```bash
sudo bash UTS.sh --lang en -y --include-monitor --include-tat
```

Alternatively, run directly in one line. This still asks for confirmation:

```bash
curl -fsSL https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh | sudo bash -s -- --lang en
```

Remove all three components in one line:

```bash
curl -fsSL https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh | sudo bash -s -- --lang en --include-monitor --include-tat
```

Do not remove Automation Tools from a command running inside `tat_agent`. Use SSH for `--include-tat`, or follow the detached `systemd-run` procedure in [Troubleshooting](#-troubleshooting).

> \[!TIP]
>
> If `raw.githubusercontent.com` is unreachable, switch to the jsDelivr CDN:
>
> ```bash
> curl -fsSL https://cdn.jsdelivr.net/gh/babywbx/Uninstall-Tencent-Service@main/UTS.sh | sudo bash -s -- --lang en
> ```

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 📖 Usage

Download the script, then run it with options:

```bash
curl -fsSLO https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh
sudo bash UTS.sh [options]
```

### Options

| Option | Description |
| --- | --- |
| `-y`, `--yes` | Skip the confirmation prompt. Required when no terminal is available |
| `--include-monitor` | Also uninstall Cloud Monitor (BaradAgent and Sgagent) |
| `--include-tat` | Also uninstall Automation Tools (`tat_agent`) |
| `--dry-run` | Show what would be stopped or removed. Runs no official uninstaller and changes nothing |
| `--force` | Keep cleaning after failures, within the limits described below |
| `--timeout SECONDS` | Timeout for each managed command, including system queries, `1` to `3600` seconds, default `120`. Where `timeout -k` is supported, a `5` second grace period precedes SIGKILL |
| `--lang zh\|en` | Output language, default `zh`. Use `en` on terminals that cannot render Chinese, such as VNC |
| `-V`, `--version` | Show the version |
| `-h`, `--help` | Show the help message |

### Output

The script first shows the system, mode and plan. Each component is marked `[remove]`, `[keep]`, `[clean]` or `[skip]`, together with the console features the removal affects. After confirmation it runs in `[n/N]` steps and ends with a per-component summary. An excerpt:

```text
Uninstall-Tencent-Service 1.0.0
System: TencentOS Server 4, x86_64
Mode: uninstall

==> Plan
  [remove] Host Security agent (YunJing)
  [keep]   Cloud Monitor (BaradAgent / Sgagent)
           Installed; add --include-monitor to uninstall it too.
  [keep]   TencentCloud Automation Tools (tat_agent)
           Installed; add --include-tat to uninstall it too.

  The displayed official uninstall verification code will be entered automatically.
  The console then raises an agent uninstall alert.
  To preview without changes, run again with --dry-run.

Continue? [y/N] y

==> [1/2] Host Security agent (YunJing)
...
==> Summary
[ OK ] Host Security agent (YunJing): uninstalled
[SKIP] Cloud Monitor (BaradAgent / Sgagent): kept, not selected
[SKIP] TencentCloud Automation Tools (tat_agent): kept, not selected
```

Each line carries an `[INFO]`, `[ OK ]`, `[WARN]`, `[FAIL]` or `[SKIP]` tag. Colors are enabled on capable terminals and disabled when output is redirected, `TERM` is `dumb`, or `NO_COLOR` is set. In `--dry-run` mode, local actions start with `[DRY-RUN]` and list the processes actually matched and the files that exist.

Only `y` or `yes` confirms; any other answer cancels without changing anything. If the official uninstaller displays its 6-digit confirmation code, the script enters it automatically after confirmation or `-y`. It recognizes the official Chinese and English prompts, avoids submitting the same bilingual prompt twice, and handles a refreshed code. Unrelated six-digit numbers are not treated as confirmation codes.

### Force Mode

```bash
sudo bash UTS.sh --force
```

Start with the default mode. Use `--force` when an ordinary official uninstaller failure or persistent remnants need scoped local cleanup. A Host Security failure stops later components by default; an ordinary failure in an optional component is recorded, while other selected components can still continue. `--force` relaxes the following:

- After an ordinary official uninstaller failure, scoped local cleanup continues. A timeout (`124`), SIGKILL (`137`) or signal interruption still stops the run.
- When a service stop request fails, process checks continue. Verified agent processes still running 5 seconds after TERM receive SIGKILL.
- Selected systemd units are masked before stopping, so they cannot be reactivated during cleanup. Existing definitions in `/etc/systemd/system` are first backed up to root-only `/var/backups/uts-units.*`. This run's backup is removed only after every step and the final verification succeed; failures and interruptions keep it. Backups from earlier runs are left untouched. Masks created by the script are recorded in `/var/lib/uts/created-masks` and removed once the component is cleaned; after a failure they stay, and a later successful run releases them. Pre-existing masks are left alone.
- When removal fails, only the `i` (immutable) and `a` (append-only) attributes actually found on the selected component's paths are cleared, then removal is retried once. Symlinks are skipped; mounted files and files with multiple hard links are refused.

`--force` does not bypass path, mount, kernel protection or timeout checks. It still stops when the official uninstaller reports success but removal is incomplete, when the kernel protection device `/dev/txyd` remains, or when an install directory is a symlink or contains a mount point.

### Exit Status

| Status | Meaning |
| --- | --- |
| `0` | Local uninstall of the selected components verified; in dry-run mode, the preview finished |
| `1` | A step failed, remnants remain, or the run was refused or canceled |
| `2` | Invalid usage, or no terminal without `-y`; nothing was changed |
| `6` | Uninstall protection, kernel protection or an unstoppable process kept the Host Security agent |
| `124` | A managed command timed out; later steps did not run |
| `137` | A managed command received SIGKILL, either from timeout escalation or an external source; later steps did not run |
| `130`, `143` | Interrupted. Nothing was changed if no step had started; otherwise run the script again to finish |

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🔩 How It Works

1. Parse options. Without a usable terminal and without `-y` or `--dry-run`, exit with status `2`
2. Check Linux, root privileges, tool dependencies and `/proc`, and detect the distribution. A real run takes an exclusive lock and creates a private work directory
3. Preflight the selected install directories, refusing symlinks and directories that contain mount points. If the script was launched by a process of a component being removed, or runs inside that component's service cgroup (for example, from the Automation Tools console with `--include-tat`), exit before changing anything
4. Show the plan and wait for confirmation
5. Host Security: run the local official `uninst.sh` (looking in `/usr/local/qcloud/YunJing` first, then `/var/lib/qcloud/YunJing`) and enter the confirmation code if it displays one. Output buffering is disabled with `stdbuf` so the prompt can be read through a pipe. If the official program reports success but removal is incomplete after a short recheck, return `6` before local cleanup. Otherwise remove the keep-alive jobs in `/etc/cron.d/yunjing` and the root crontab, stop remaining processes, remove known install and runtime directories such as `/var/cache/ydeyes`, installer packages in `/tmp`, PID files, install logs and cgroups, then check for agent reappearance during a 5-second elapsed-time window
6. Cloud Monitor (`--include-monitor`): following the official order, run BaradAgent's `barad/admin/uninstall.sh`, then Sgagent's `stargate/admin/uninstall.sh`. Remove the keep-alive jobs in the root crontab and `/etc/cron.d/sgagenttask`, stop the `stargate` service and processes, and remove `stargate`, `monitor/barad`, `monitor/python26`, `monitor/config` and the `cagent_tools` link
7. Automation Tools (`--include-tat`): perform cleanup locally without fetching an external uninstaller. Stop and remove the services and startup entries of `tat_agent`, `tat_agent_service` and `tat_install`, stop the processes, and remove the install directory, `/tmp/tat_agent`, links such as `/usr/local/bin/tat_agent`, and PID files
8. Verify known install directories, processes, service and startup files, systemd unit states, cron jobs and leftover files. Only after all steps and final verification succeed, remove this run's service backup. Summarize per component and return the matching status

The 5-second window is not a limit on total runtime. A scan already in progress and the final verification can take longer. `--timeout` applies to individual managed commands, not the whole scan or run. These checks cover the known components and the observed interval; they cannot guarantee that a later cloud-side policy will never reinstall an agent.

Key safety rules:

- An official uninstaller and its parent directories must be owned by root, not symlinks, and not writable by group or others, and the file must start with `#!` or be an ELF binary. Only then is it run, from its own directory. Otherwise it is skipped in favor of local cleanup
- Agent processes are identified by the real path of `/proc/<pid>/exe`, or by `sh`, `bash` and similar interpreters running known scripts from a component directory, never by fuzzy process names. Candidates are rechecked during scanning; before signaling, the executable path, start time and command line checksum are checked again
- When a systemd unit has vendor stop hooks, a temporary override under `/run/systemd/system` clears them before stopping, so vendor stop scripts never run. Upstart jobs with `pre-stop` or `post-stop` keep the component and report a failure
- Only cron lines confirmed to belong to a selected component are removed. When a command is too complex to confirm, the file is kept and manual review is requested
- Crontab and `/etc/cron.d` files are compared again before writing; if they changed in the meantime, the newer version is kept

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🛠 Troubleshooting

### Status 6: the agent was not removed

Enabled **Agent uninstall protection** alone is not an error: the script normally answers the official prompt and completes removal. Status `6` means that Host Security could not be removed or reappeared. Read the preceding messages to identify whether the official program left an agent running, a process could not be stopped, or protection remained.

If official removal still cannot complete, turn protection off in the [Host Security console][console-link] or remove this server from its scope, wait about a minute, then retry.

If the script reports that the kernel protection device `/dev/txyd` remains, uninstall the agent from the Host Security console first, then run the script.

### No terminal for confirmation

Without a terminal (cron, CI or remote commands), the script cannot ask for confirmation and exits with status `2`. Add `-y` when you are sure, or preview with `--dry-run` first.

### Removing Automation Tools through Automation Tools

When run from the Automation Tools console with `--include-tat`, the script prints `Run from an SSH session or a detached unit such as systemd-run` and exits. Log in over SSH, or save the downloaded script as `/root/UTS.sh` and launch it in a separate systemd unit:

```bash
sudo systemd-run --unit=uts /bin/bash /root/UTS.sh -y --lang en --include-monitor --include-tat
```

```bash
sudo journalctl -u uts -f
```

`systemd-run` starts the job asynchronously; successful launch does not mean uninstall succeeded. Follow the journal over SSH and check the final summary. A cloud command may time out after `tat_agent` is removed, so its timeout is not the script's result. Use the journal and the systemd unit's exit status to determine the outcome.

### Install directory is not owned by root

The Cloud Monitor `stargate` directory is extracted by the vendor package as uid 501, so this warning is expected. The script skips the official uninstaller in that directory and cleans up locally; the result is not affected.

### Timeouts or interruptions

`--timeout` limits each managed command, not the whole run. This includes system queries and reading the root crontab. These operations can be interrupted, and timeout or signal statuses are preserved. Status `124` indicates a timeout; `137` indicates SIGKILL and can also come from an external source; `143` indicates SIGTERM. After the official uninstaller fails this way, local cleanup does not continue, including in force mode. Check the failed command in the log before retrying.

After an interruption, rerun the script to finish cleanup. Masks left by force mode are released by a later successful run. Service backups from failed or interrupted runs remain available; only the current run's backup is deleted after full success.

### Effects of uninstalling

- Without Host Security, the server shows as offline in the Host Security console, and a client uninstall alert is raised
- Without Cloud Monitor, metrics that depend on its agents, such as memory and disk metrics, stop updating in the console
- Without Automation Tools, console commands and online password reset stop working; SSH remains available

To restore them, reinstall the components following the official documentation.

### Local checks and tests

In the repository directory, run:

```bash
bash -n UTS.sh
shellcheck UTS.sh
uv run --no-project python -m unittest discover -s tests -v
```

The tests isolate service changes and file removal in temporary fixtures. They also exercise controlled real subprocesses, signals, timeouts and pseudo-terminals, including cleanup after interruption. They require no cloud server and do not uninstall real agents. Tests that need Linux root or unavailable system tools are skipped when those requirements are not met. CI runs the same checks on Ubuntu for pushes to `main` and for pull requests.

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 📚 References

- [Tencent Cloud: Host Security quick start, including client uninstall (Chinese)][doc-yunjing-link]
- [Tencent Cloud: Uninstalling, restarting and stopping CVM monitoring agents (Chinese)][doc-monitor-link]
- [Tencent Cloud: Automation Tools FAQ, including client uninstall (Chinese)][doc-tat-link]
- [Tencent Cloud: Installing the Automation Tools client (Chinese)][doc-tat-install-link]

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 📝 License

Copyright © 2018-present [Babywbx][profile-link].<br/>
This project is licensed under the [MIT](./LICENSE) License.

<!-- LINK GROUP -->

[back-to-top]: https://img.shields.io/badge/-BACK_TO_TOP-151515?style=flat-square
[console-link]: https://console.cloud.tencent.com/cwp
[doc-monitor-link]: https://cloud.tencent.com/document/product/248/53584
[doc-tat-install-link]: https://cloud.tencent.com/document/product/1340/51945
[doc-tat-link]: https://cloud.tencent.com/document/product/1340/50822
[doc-yunjing-link]: https://cloud.tencent.com/document/product/296/12236
[github-ci-link]: https://github.com/babywbx/Uninstall-Tencent-Service/actions/workflows/ci.yml
[github-ci-shield]: https://img.shields.io/github/actions/workflow/status/babywbx/Uninstall-Tencent-Service/ci.yml?branch=main&label=CI&labelColor=black&style=flat-square
[github-contributors-link]: https://github.com/babywbx/Uninstall-Tencent-Service/graphs/contributors
[github-contributors-shield]: https://img.shields.io/github/contributors/babywbx/Uninstall-Tencent-Service?color=c4f042&labelColor=black&style=flat-square
[github-forks-link]: https://github.com/babywbx/Uninstall-Tencent-Service/network/members
[github-forks-shield]: https://img.shields.io/github/forks/babywbx/Uninstall-Tencent-Service?color=8ae8ff&labelColor=black&style=flat-square
[github-issues-link]: https://github.com/babywbx/Uninstall-Tencent-Service/issues
[github-issues-shield]: https://img.shields.io/github/issues/babywbx/Uninstall-Tencent-Service?color=ff80eb&labelColor=black&style=flat-square
[github-lastcommit-link]: https://github.com/babywbx/Uninstall-Tencent-Service/commits/main
[github-lastcommit-shield]: https://img.shields.io/github/last-commit/babywbx/Uninstall-Tencent-Service?labelColor=black&style=flat-square
[github-license-link]: https://github.com/babywbx/Uninstall-Tencent-Service/blob/main/LICENSE
[github-license-shield]: https://img.shields.io/github/license/babywbx/Uninstall-Tencent-Service?color=white&labelColor=black&style=flat-square
[github-stars-link]: https://github.com/babywbx/Uninstall-Tencent-Service/stargazers
[github-stars-shield]: https://img.shields.io/github/stars/babywbx/Uninstall-Tencent-Service?color=ffcb47&labelColor=black&style=flat-square
[profile-link]: https://github.com/babywbx
