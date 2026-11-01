<div align="center"><a name="readme-top"></a>

# Uninstall Tencent Service

一键卸载腾讯云主机安全（云镜 YunJing）Agent 并清理残留的 Bash 脚本，<br/>
可选同时卸载云监控（BaradAgent / Sgagent）和自动化助手（TAT）。

[English](./README.en.md) · [报告问题][github-issues-link] · [参考文档][doc-yunjing-link]

<!-- SHIELD GROUP -->

[![][github-stars-shield]][github-stars-link]
[![][github-forks-shield]][github-forks-link]
[![][github-issues-shield]][github-issues-link]
[![][github-license-shield]][github-license-link]<br/>
[![][github-contributors-shield]][github-contributors-link]
[![][github-lastcommit-shield]][github-lastcommit-link]
[![][github-ci-shield]][github-ci-link]

</div>

> \[!TIP]
>
> **在找阿里云的方案吗？** 请使用同系列的 [Uninstall Aliyun Service][uas-link]，一键卸载阿里云云安全中心（云盾）Agent，可选同时卸载云助手、云监控和 LoongCollector。

<details>
<summary><kbd>目录</kbd></summary>

#### TOC

- [📋 概述](#-概述)
- [⚠️ 前置条件](#️-前置条件)
- [🚀 快速开始](#-快速开始)
- [📖 用法](#-用法)
- [🔩 执行流程](#-执行流程)
- [🧪 实测记录](#-实测记录)
- [🛠 故障排查](#-故障排查)
- [🔗 相关项目](#-相关项目)
- [📚 参考资料](#-参考资料)
- [📝 许可证](#-许可证)

####

<br/>

</details>

## 📋 概述

腾讯云镜像可能预装主机安全、云监控和自动化助手，并通过定时任务和服务保活。这个脚本先运行服务器上已有的官方卸载程序，再清理已知残留，结束前逐项检查文件、进程、服务和定时任务。

- 🛡️ 默认卸载主机安全 Agent（原云镜，`YDService` / `YDLive`）
- 📊 加 `--include-monitor` 同时卸载云监控（`barad_agent` / `sgagent`）
- 🤖 加 `--include-tat` 同时卸载自动化助手（`tat_agent`）
- 🧹 清理官方卸载程序遗留的定时任务、服务单元、运行时目录、安装包、PID 文件和 cgroup
- 🔍 卸载后验证文件、进程、服务和定时任务，并复查 Agent 是否重新出现
- 🌐 不下载额外的卸载脚本，默认中文输出，`--lang en` 切换英文
- ✅ 已在 140 多台腾讯云服务器、38 个公共镜像上实测，见[实测记录](#-实测记录)

脚本只处理上述三个组件的已知路径。`/usr/local/qcloud` 下镜像初始化用的其他文件保持不变，脚本也不会修改控制台设置或重启系统。

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## ⚠️ 前置条件

1. 当前系统为 Linux，已安装 `bash`
2. 使用 `root` 或具备等效 `sudo` 权限的账户执行
3. 已安装 `awk`、`readlink`、`stat`、`grep`、`cksum`、`find`、`timeout`、`flock`、`mkfifo` 和 `stdbuf`，并且可以读取 Linux `/proc`

脚本会检查依赖，但不会自动安装软件。`timeout` 不支持 `-k` 时（如 CentOS 6），超时的命令直接收到 SIGKILL，脚本会给出提示。`--force` 在修复阻止删除的文件属性时还需要 `lsattr` 和 `chattr`。

> \[!IMPORTANT]
>
> **开启 Agent 卸载保护时也可以使用。** 你确认卸载或传入 `-y` 后，脚本会自动填写官方卸载程序显示的 6 位验证码，无需先关闭保护。`--dry-run` 不运行官方程序，也不会填写验证码。
>
> 如果官方卸载仍未完成、内核保护仍在或进程无法停止，脚本会报错，不会仅凭官方程序返回 `0` 就判定成功。返回 `6` 时请按[故障排查](#返回-6agent-未被卸载)处理。
>
> 卸载完成后，控制台会产生客户端卸载告警，这是正常现象。

### 支持的系统

| 类别 | 覆盖范围 |
| --- | --- |
| 发行版 | TencentOS Server、OpenCloudOS、CentOS、CentOS Stream、Rocky Linux、AlmaLinux、Fedora、openSUSE Leap、Debian、Ubuntu、CoreOS |
| 初始化系统 | systemd、SysVinit（`chkconfig` / `update-rc.d`）、OpenRC（`rc-update`）、Upstart |
| 安装路径 | 标准路径 `/usr/local/qcloud/...`，以及 `/usr` 不可写时（如 CoreOS）使用的 `/var/lib/qcloud/...` |
| CPU 架构 | x86_64、aarch64 |
| Bash 版本 | 4.1 及以上 |
| coreutils | GNU coreutils，以及 Ubuntu 26.04 默认的 uutils coreutils |

各系统的实测情况见[实测记录](#-实测记录)。

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🚀 快速开始

先通过 **SSH** 登录目标服务器。推荐先下载并预览，确认组件范围后再执行：

```bash
curl -fsSL https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh -o UTS.sh &&
  sudo bash UTS.sh --dry-run
```

仅卸载主机安全，保留云监控和自动化助手：

```bash
sudo bash UTS.sh
```

如果三个组件都要卸载，预览和执行使用同一组选项：

```bash
sudo bash UTS.sh --dry-run --include-monitor --include-tat
sudo bash UTS.sh --include-monitor --include-tat
```

脚本会显示执行计划并等待确认。检查最后的逐组件汇总和退出状态；发现失败或残留时，按提示处理后重新运行。

无人值守时加 `-y`，跳过确认并授权填写官方验证码：

```bash
sudo bash UTS.sh -y --include-monitor --include-tat
```

也支持一行执行，仍会在可用终端上询问确认：

```bash
curl -fsSL https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh | sudo bash
```

一行同时卸载三个组件：

```bash
curl -fsSL https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh | sudo bash -s -- --include-monitor --include-tat
```

不要通过自动化助手控制台直接卸载 TAT 自身。请用 SSH，或按[独立单元运行方式](#通过自动化助手卸载自动化助手)操作。

> \[!TIP]
>
> 如果 `raw.githubusercontent.com` 无法访问，把地址换成 jsDelivr CDN：
>
> ```bash
> curl -fsSL https://cdn.jsdelivr.net/gh/babywbx/Uninstall-Tencent-Service@main/UTS.sh | sudo bash
> ```

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 📖 用法

下载脚本后，带参数运行：

```bash
curl -fsSLO https://raw.githubusercontent.com/babywbx/Uninstall-Tencent-Service/main/UTS.sh
sudo bash UTS.sh [options]
```

### 参数

| 选项 | 说明 |
| --- | --- |
| `-y`, `--yes` | 跳过确认提示。没有可用终端时必须使用 |
| `--include-monitor` | 同时卸载云监控（BaradAgent 和 Sgagent） |
| `--include-tat` | 同时卸载自动化助手（`tat_agent`） |
| `--dry-run` | 只预览将要停止和删除的内容，不运行官方卸载程序，不做任何修改 |
| `--force` | 出错后继续清理，范围见下文 |
| `--timeout SECONDS` | 每条受管命令的时限，接受 `1` 至 `3600` 秒，默认 `120` 秒。通常先发送 TERM，最多再等 `5` 秒后发送 KILL；不支持 `-k` 的旧工具直接发送 KILL |
| `--lang zh\|en` | 输出语言，默认 `zh`。VNC 等无法显示中文的终端请用 `en` |
| `-V`, `--version` | 显示版本 |
| `-h`, `--help` | 显示帮助信息 |

### 输出

脚本先显示系统、模式和执行计划，每个组件标明 `[卸载]`、`[保留]`、`[清理]` 或 `[跳过]`，并列出卸载对控制台功能的影响，确认后按 `[n/N]` 分步执行，最后逐个组件汇总结果。以下为节选：

```text
Uninstall-Tencent-Service 1.0.0
系统：TencentOS Server 4，x86_64
模式：卸载

==> 执行计划
  [卸载] 主机安全 Agent（YunJing）
  [保留] 云监控（BaradAgent / Sgagent）
         已安装，加 --include-monitor 可一并卸载。
  [保留] 腾讯云自动化助手（tat_agent）
         已安装，加 --include-tat 可一并卸载。

  将自动填写官方卸载程序显示的验证码。
  卸载后控制台会产生 Agent 卸载告警。
  如需只预览不修改，请加 --dry-run 重新运行。

确认卸载？[y/N] y

==> [1/2] 主机安全 Agent（YunJing）
...
==> 结果汇总
[ OK ] 主机安全 Agent（YunJing）：已卸载
[SKIP] 云监控（BaradAgent / Sgagent）：已保留，未选择
[SKIP] 腾讯云自动化助手（tat_agent）：已保留，未选择
```

每行带 `[INFO]`、`[ OK ]`、`[WARN]`、`[FAIL]` 或 `[SKIP]` 状态标签。终端支持时自动着色；输出被重定向、终端类型为 `dumb` 或设置了 `NO_COLOR` 环境变量时关闭颜色。`--dry-run` 模式下，本地操作以 `[DRY-RUN]` 开头，并列出实际命中的进程和已存在的文件。

确认只接受 `y` 或 `yes`，其他输入都会取消，取消时不做任何修改。官方卸载程序会显示一个 6 位验证码要求确认，确认卸载或传入 `-y` 后，脚本自动填写这个验证码。

### 强制模式

```bash
sudo bash UTS.sh --force
```

通常先使用默认模式。需要处理可恢复的官方卸载失败或顽固残留时，再使用 `--force`。主机安全的官方卸载失败会停止后续组件；可选组件的普通失败会被记录，其他已选组件仍可继续。`--force` 放宽以下几点：

- 官方卸载程序普通失败后，继续做限定范围的本地清理。超时（`124`、`137`）或被信号中断时仍然停止。
- 服务停止请求失败后，继续检查进程。已确认的 Agent 进程在 TERM 后 5 秒仍未退出时发送 SIGKILL。
- 停止前屏蔽（mask）所选组件的 systemd 单元，阻止清理期间被重新激活。`/etc/systemd/system` 中已有的定义先备份到仅 root 可读的 `/var/backups/uts-units.*`。仅在全部步骤和最终验证成功后删除本次备份；失败或中断时保留，历史备份不自动删除。本次创建的屏蔽记录在 `/var/lib/uts/created-masks`，组件清理成功后解除；失败时保留，之后成功的运行会自动收回。原有屏蔽保持不变。
- 删除失败时，只清除所选组件路径上实际存在的 `i`（immutable）和 `a`（append-only）属性，再重试一次。跳过符号链接，拒绝修改挂载文件和多重硬链接文件。

以下情况 `--force` 也不会继续：官方程序报告成功但卸载仍未完成、内核保护设备 `/dev/txyd` 仍然存在、安装目录是符号链接或含有挂载点。超时和信号中断也会停止后续步骤。

### 退出状态

| 状态码 | 含义 |
| --- | --- |
| `0` | 所选组件的本地卸载验证通过；预览模式下表示预览完成 |
| `1` | 有步骤失败、仍有残留，或运行被拒绝、取消 |
| `2` | 用法错误，或没有终端又没加 `-y`，未做任何修改 |
| `6` | 因卸载保护、内核保护或进程无法停止，主机安全 Agent 未被卸载 |
| `124` | 受管命令超时，后续步骤未执行 |
| `137` | 命令被 SIGKILL 终止，可能是超时强杀，也可能是外部机制终止；后续步骤未执行 |
| `130`、`143` | 被中断。尚未开始任何步骤时不会有修改，否则重新运行即可继续清理 |

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🔩 执行流程

1. 解析参数。没有可用终端又没加 `-y` 或 `--dry-run` 时，以状态码 `2` 退出
2. 检查 Linux、root 权限、工具依赖和 `/proc`，识别发行版。正式执行时获取独占锁，并创建私有临时目录
3. 预检所选安装目录，拒绝符号链接和含有挂载点的目录。脚本由待卸载组件的进程启动，或运行在其服务 cgroup 中时（例如加 `--include-tat` 后从自动化助手控制台执行），在修改任何内容之前退出
4. 显示执行计划并等待确认
5. 主机安全：运行本机的官方 `uninst.sh`（先找 `/usr/local/qcloud/YunJing`，再找 `/var/lib/qcloud/YunJing`），自动填写它显示的验证码。官方程序报告成功但卸载未完成时返回 `6`。之后删除 `/etc/cron.d/yunjing` 和 root crontab 中的保活任务，停止残留进程，删除安装目录、`/var/cache/ydeyes` 等运行时目录、`/tmp` 中的安装包、PID 文件、安装日志和 cgroup，最后在约 5 秒的观察窗口内检查 Agent 是否重新出现
6. 云监控（`--include-monitor`）：按官方顺序先运行 BaradAgent 的 `barad/admin/uninstall.sh`，再运行 Sgagent 的 `stargate/admin/uninstall.sh`。删除 root crontab 和 `/etc/cron.d/sgagenttask` 中的保活任务，停止 `stargate` 服务和进程，删除 `stargate`、`monitor/barad`、`monitor/python26`、`monitor/config` 以及 `cagent_tools` 链接
7. 自动化助手（`--include-tat`）：官方卸载脚本需要联网下载，脚本按相同的步骤在本地完成。停止并删除 `tat_agent`、`tat_agent_service`、`tat_install` 的服务和启动项，停止进程，删除安装目录、`/tmp/tat_agent`、`/usr/local/bin/tat_agent` 等链接和 PID 文件
8. 验证安装目录、进程、服务与启动文件、systemd 单元状态、定时任务和残留文件。全部通过后清理本次生成的服务备份，再按组件汇总结果并返回相应状态

观察窗口按实际经过的时间计算，包含进程扫描耗时；最后一次完整扫描可能使总时长略超 5 秒。退出 `0` 表示本次本地检查通过，不保证将来没有其他程序或控制台策略再次安装 Agent。

几个关键的安全规则：

- 官方卸载程序及其上级目录都必须属于 root、不是符号链接、不可被组或其他用户写入，文件头必须是 `#!` 或 ELF，满足后才在其所在目录执行。不满足条件时跳过官方程序，改为本地清理
- 按 `/proc/<pid>/exe` 的真实路径，或 `sh`、`bash` 等解释器运行组件目录中的已知脚本来识别 Agent 进程，不按进程名模糊匹配。扫描时复核候选进程，发送信号前再次用真实路径、启动时间和命令行校验和确认身份
- systemd 单元带有厂商停止钩子时，先通过 `/run/systemd/system` 下的临时覆盖清空这些钩子再停止，不执行厂商的停止脚本。Upstart 任务带有 `pre-stop` 或 `post-stop` 时保留该组件并报告失败
- 定时任务只删除能确认指向所选组件的行。命令过于复杂、无法确认时，保留原文件并提示人工检查
- 修改 crontab 和 `/etc/cron.d` 文件前再次比对内容，期间有其他改动时保留较新的版本

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🧪 实测记录

项目已在 140 多台腾讯云服务器上进行多轮实测，覆盖 11 个发行版系列的 38 个公共镜像，其中 5 个是 ARM64。测试包含三个组件全部安装、仅安装部分组件和没有 Agent 的情况，并结合不同权限、保护状态和运行方式验证清理结果。

### 测试内容

| 项目 | 做法 |
| --- | --- |
| 卸载组合 | 默认参数，逐个加上 `--include-monitor` 和 `--include-tat`，一次全选，以及 `--force` |
| 重跑与中断 | 每次卸载后立即重跑一遍；执行中途发送中断信号，再重新运行完成清理 |
| 残留检查 | 卸载前后核对进程、服务、定时任务和 cgroup，并在整个文件系统中搜索 Agent 相关的文件和配置引用 |
| 防误操作 | 查看版本、没有终端、在终端或管道中取消确认、预览、英文预览，取消后确认系统没有任何改动 |
| 卸载保护 | 分别在开启和关闭「Agent 卸载保护」时执行 |
| 长时间观察 | 卸载后观察 10 分钟，再重启并观察 5 分钟，检查观察期内是否重装 |
| 自动化助手 | 从控制台下发预览和默认卸载，确认加 `--include-tat` 时会拒绝，再用 `systemd-run` 完整卸载 |
| 自动化测试 | 在 Linux 上分别以 root 和普通用户运行，覆盖路径安全、进程身份、超时中断、定时任务解析和终端交互；并在 40 台实机上各跑一遍完整测试套件，80 次全部通过 |
| 备份清理 | 验证成功后删除本次生成的备份；失败时保留恢复材料，不误删已有备份 |
| 进程扫描 | 使用千级进程的隔离目录验证观察窗口耗时和进程身份复核 |

### 覆盖范围

已验证的发行版与版本如下，各项场景按适用环境组合测试：

| 发行版 | 版本 |
| --- | --- |
| TencentOS Server | 2.4、3.1、3.3、4，以及 4（ARM64） |
| OpenCloudOS | 7、8、9，以及 9（ARM64） |
| CentOS | 6.10、7.9、8.5，以及 7.9（ARM64） |
| CentOS Stream | 9、10 |
| Rocky Linux | 8.10、9.8、10.2 |
| AlmaLinux | 8.10、9.8、10.2 |
| Fedora | 40 |
| openSUSE Leap | 15.6 |
| Debian | 9、10、11、12、13，以及 11（ARM64） |
| Ubuntu | 14.04、16.04、18.04、20.04、22.04、24.04、26.04，以及 22.04（ARM64） |
| CoreOS | 1745.5.0 |

实机测试覆盖防卸载开关、强制卸载、预览与取消、TAT 控制台拒绝、残留清理和重复执行。持续观察及重启复查均未发现重装，结论对应上述观察窗口。

观察窗口按实际耗时计算，避免在进程较多时固定重复全量扫描，同时保留进程身份复核和最终检查。

### 实测发现

- **Agent 卸载保护**：开启后，官方程序可能要求输入验证码。实测自动填写后可以正常卸载；交互没有完成或保护仍阻止清理时才会停止并提示处理。
- **验证码**：官方 `uninst.sh` 是编译好的程序，会显示 6 位验证码要求确认。脚本在你确认后自动填写，`--dry-run` 不会运行它。
- **输出缓冲**：某些官方程序接到管道后会缓存验证码提示，导致等待输入却不显示提示。脚本使用 `stdbuf` 关闭该程序的输出缓冲，支持无换行提示，并避免同一个中英文验证码重复提交。
- **官方程序的遗留**：主机安全卸载后会留下 `/var/cache/ydeyes`、安装日志、PID 文件、cgroup 和 `/tmp` 中的安装包；云监控官方卸载不删 `monitor/python26` 和 `monitor/config`；自动化助手的官方脚本不删 `/usr/local/bin/tat_agent`。脚本会一并清理。
- **Sgagent 文件属主**：云监控安装包中的 `stargate` 目录属主为 uid 501 且组可写，脚本会提示「安装目录不属于 root」，并跳过 Sgagent 自带的卸载程序，改为本地清理。这是预期行为。
- **老系统**：Ubuntu 14.04（Upstart）和 CentOS 6.10（Bash 4.1，`timeout` 不支持 `-k`）都可以正常卸载。
- **自动化助手控制台**：从控制台执行默认卸载或预览没有问题。加 `--include-tat` 时脚本会拒绝，因为停止 `tat_agent` 会连同脚本一起终止，改用 SSH 或 `systemd-run` 即可，见[故障排查](#-故障排查)。

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🛠 故障排查

### 返回 6：Agent 未被卸载

先查看返回 `6` 前的具体提示。开启 **Agent 卸载保护** 本身不代表失败，已知验证码交互会自动处理。如果官方卸载仍未完成，可以在[主机安全控制台][console-link]将本机移出保护范围或关闭相应保护，等待策略下发后重跑。

如果提示内核保护设备 `/dev/txyd` 仍在，请先在主机安全控制台卸载 Agent，再运行本脚本。

### 提示没有可用于确认的终端

脚本在没有终端的环境中（如 cron、CI 或远程命令）无法确认，会以状态码 `2` 退出。确认要执行时加 `-y`，或先用 `--dry-run` 预览。

### 通过自动化助手卸载自动化助手

从自动化助手控制台执行并加 `--include-tat` 时，脚本会提示 `请在 SSH 会话或 systemd-run 等独立单元中运行` 并退出。请通过 SSH 登录执行，或在独立单元中运行：

```bash
systemd-run --unit=uts /bin/bash /root/UTS.sh -y --include-monitor --include-tat
```

```bash
journalctl -u uts -f
```

`systemd-run` 返回只表示任务已启动，不代表卸载已完成。通过 SSH 查看日志中的最终汇总和 systemd 记录的退出状态。自动化助手被卸载后，控制台中的这次命令可能显示超时，不能把控制台任务的状态当作脚本的最终结果。

### 提示安装目录不属于 root

云监控的 `stargate` 目录由厂商安装包以 uid 501 解压，这条提示是预期的。脚本会跳过该目录中的官方卸载程序，改为本地清理，不影响卸载结果。

### 操作超时或任务被中断

`--timeout` 限制的是单条受管命令，不是整次运行的总时长。超时或信号终止会停止后续步骤。`137` 也可能来自厂商保护或外部 SIGKILL，不能只凭这个数字断定超时。请先检查日志中的失败命令，处理原因后重新运行；强制模式留下的屏蔽会在之后成功的运行中收回。

### 卸载后的影响

- 卸载主机安全后，主机安全控制台中该主机显示为离线，并产生客户端卸载告警
- 卸载云监控后，控制台中依赖 Agent 的内存、磁盘等监控指标停止更新
- 卸载自动化助手后，依赖 TAT 的控制台执行命令和在线重置密码不可用，已有的 SSH 登录方式仍可使用

需要恢复时，按官方文档重新安装对应组件。

### 本地检查与测试

在仓库目录运行：

```bash
bash -n UTS.sh
shellcheck UTS.sh
uv run --no-project python -m unittest discover -s tests -v
```

测试使用临时目录和模拟命令隔离文件、服务及定时任务操作，并启动受控子进程验证真实超时、信号和伪终端交互，不会卸载主机上的腾讯云 Agent。部分测试需要 Linux root、GNU 或 uutils `timeout`、`flock`，输出缓冲回归还需要 `cc` 和 `stdbuf`；不满足时会明确跳过。推送到 `main` 和提交 Pull Request 时，CI 会在 Ubuntu 上运行同样的检查。

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 🔗 相关项目

- [Uninstall Aliyun Service][uas-link]：本项目的阿里云版本。一键卸载阿里云云安全中心（云盾）Agent，可选同时卸载云助手、云监控和 LoongCollector，同样先调用官方卸载程序再清理残留，并在阿里云多个发行版上实测过。

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 📚 参考资料

- [腾讯云：主机安全快速入门（含卸载客户端）][doc-yunjing-link]
- [腾讯云：卸载、重启、停止云服务器监控组件][doc-monitor-link]
- [腾讯云：自动化助手常见问题（含卸载客户端）][doc-tat-link]
- [腾讯云：安装自动化助手客户端][doc-tat-install-link]

<div align="right">

[![][back-to-top]](#readme-top)

</div>

## 📝 许可证

Copyright © 2018-present [Babywbx][profile-link].<br/>
本项目基于 [MIT](./LICENSE) 许可证发布。

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
[uas-link]: https://github.com/babywbx/Uninstall-Aliyun-Service
