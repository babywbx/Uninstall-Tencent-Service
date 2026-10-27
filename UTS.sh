#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_NAME="Uninstall-Tencent-Service"
readonly VERSION="1.0.0"
WORKDIR=""
COMMAND_TIMEOUT=120
TIMEOUT_KILL=(-k 5)
FAILURES=0
RUN_PID=""
LOCK_DIR="/run/uts.lock"
PROC_ROOT="/proc"
YUNJING_DIRS=(/usr/local/qcloud/YunJing /var/lib/qcloud/YunJing)
YUNJING_RUNTIME=(/run/ydeyes /var/run/ydeyes)
YUNJING_FILES=(/tmp/yunjing_install /tmp/yunjing_install_nohup)
STARGATE_DIRS=(/usr/local/qcloud/stargate /var/lib/qcloud/stargate)
MONITOR_DIRS=(/usr/local/qcloud/monitor /var/lib/qcloud/monitor)
MONITOR_FILES=(/tmp/stargate.lock /var/run/stargate.tencentyun.pid /var/run/stargat.tencentyun.pid)

AUTO_YES=0
INCLUDE_MONITOR=0
DRY_RUN=0
UI_LANG=zh
MSG=""

DISTRO_PRETTY="unknown"

if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'
  C_BLUE=$'\033[34m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_RED=$'\033[31m'
else
  C_RESET=''; C_BOLD=''
  C_BLUE=''; C_GREEN=''; C_YELLOW=''; C_RED=''
fi
readonly C_RESET C_BOLD C_BLUE C_GREEN C_YELLOW C_RED

cleanup() {
  if [[ -n "${WORKDIR}" ]]; then
    rm -rf -- "${WORKDIR}"
  fi
}

interrupt() {
  trap '' INT TERM
  if [[ -n "${RUN_PID}" ]]; then
    kill -TERM "${RUN_PID}" 2>/dev/null || true
    wait "${RUN_PID}" 2>/dev/null || true
  fi
  warn "Interrupted; run the script again to finish the cleanup." "已中断，重新运行本脚本即可继续清理。" || true
  exit "$1"
}

msg() {
  MSG="$1"
  [[ "${UI_LANG}" != zh || $# -lt 2 ]] || MSG="$2"
}

usage() {
  if [[ "${UI_LANG}" == zh ]]; then
    cat <<HELP
${SCRIPT_NAME} ${VERSION}
卸载 Linux 服务器上腾讯云预装的 Agent。

用法：./UTS.sh [选项]

选项：
  -y, --yes                跳过确认提示（没有终端时必须使用）
      --include-monitor    同时卸载云监控（BaradAgent 和 Sgagent）
      --dry-run            只预览将要停止和删除的内容，不做任何修改
      --timeout 秒数       每条受管命令的超时，1 到 3600（默认 120）
  -h, --help               显示本帮助

主机安全（YunJing）始终会被卸载。脚本先运行主机上的官方卸载程序，再停止、
删除并验证剩余内容。
运行前请在主机安全控制台关闭「Agent 卸载保护」，或将本机移出保护范围。

退出码：
  0         卸载已验证，或预览完成
  1         有步骤失败、仍有残留，或运行被拒绝、取消
  6         因卸载保护或进程无法停止，主机安全 Agent 未被卸载
  124、137  命令超时被终止
  130、143  被中断
HELP
    return
  fi
  cat <<HELP
${SCRIPT_NAME} ${VERSION}
Remove Tencent Cloud preinstalled agents from a Linux server.

Usage: ./UTS.sh [options]

Options:
  -y, --yes                Skip the confirmation prompt (required without a terminal)
      --include-monitor    Also uninstall Cloud Monitor (BaradAgent and Sgagent)
      --dry-run            Show what would be stopped or removed, change nothing
      --timeout SECONDS    Timeout for each managed command, 1 to 3600 (default: 120)
  -h, --help               Show this help message

Host Security (YunJing) is always uninstalled. Runs the official uninstallers found
on the host, then stops, removes and verifies the remnants locally.
Disable Agent uninstall protection in the Host Security console before running.

Exit status:
  0         uninstall verified, or dry run finished
  1         a step failed, remnants remain, or the run was refused or canceled
  6         uninstall protection or an unstoppable process kept Host Security
  124, 137  a command timed out and was stopped
  130, 143  interrupted
HELP
}

log() {
  msg "$@"
  printf '%s[INFO]%s %s\n' "${C_BLUE}" "${C_RESET}" "${MSG}"
}

warn() {
  msg "$@"
  printf '%s[WARN]%s %s\n' "${C_YELLOW}" "${C_RESET}" "${MSG}" >&2
}

die() {
  msg "$@"
  printf '%s[FAIL]%s %s\n' "${C_RED}" "${C_RESET}" "${MSG}" >&2
  exit 1
}

ok() {
  msg "$@"
  printf '%s[ OK ]%s %s\n' "${C_GREEN}" "${C_RESET}" "${MSG}"
}

preview() {
  msg "$@"
  printf '[DRY-RUN] %s\n' "${MSG}" >&3
}

section() {
  msg "$@"
  printf '\n%s==>%s %s%s%s\n' "${C_BOLD}${C_BLUE}" "${C_RESET}" "${C_BOLD}" "${MSG}" "${C_RESET}"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

run() {
  if [[ "${DRY_RUN}" -eq 0 ]]; then
    local rc=0 command=""
    timeout "${TIMEOUT_KILL[@]}" "${COMMAND_TIMEOUT}" "$@" </dev/null 9>&- &
    RUN_PID=$!
    wait "${RUN_PID}" || rc=$?
    RUN_PID=""
    if [[ "${rc}" -ne 0 ]]; then
      command="$(printf ' %q' "$@")"
      warn "Command failed (${rc}):${command}" "命令失败（${rc}）：${command# }"
    fi
    return "${rc}"
  fi

  printf '[DRY-RUN]' >&3
  printf ' %q' "$@" >&3
  printf '\n' >&3
}

record_failure() {
  warn "$@"
  FAILURES=1
}

require_linux() {
  if [[ "$(uname -s)" != Linux ]]; then
    die "Only Linux is supported. No uninstall was attempted." "仅支持 Linux，未执行任何卸载。"
  fi
}

prepare_runtime() {
  local tool=""
  for tool in awk readlink stat grep cksum find timeout flock; do
    command_exists "${tool}" || die "Required command missing: ${tool}. Install it before retrying." "缺少必需命令 ${tool}，请安装后重试。"
  done
  timeout -k 1 1 true 2>/dev/null || die "GNU timeout with -k is required. Install coreutils." "需要支持 -k 的 GNU timeout，请安装 coreutils。"
  [[ -r "${PROC_ROOT}/self/status" ]] || die "A readable Linux /proc is required." "需要可读的 Linux /proc。"
  [[ "${DRY_RUN}" -eq 0 ]] || return 0
  [[ -d /run ]] || LOCK_DIR="/var/run/uts.lock"
  if [[ ! -e "${LOCK_DIR}" && ! -L "${LOCK_DIR}" ]]; then
    mkdir -m 700 -- "${LOCK_DIR}" || die "Cannot create ${LOCK_DIR}." "无法创建 ${LOCK_DIR}。"
  fi
  [[ -d "${LOCK_DIR}" && ! -L "${LOCK_DIR}" ]] || die "Unsafe lock directory: ${LOCK_DIR}." "锁目录不安全：${LOCK_DIR}。"
  [[ "$(stat -c '%u:%a' "${LOCK_DIR}")" == 0:700 ]] || die "Lock directory must be owned by root with mode 700." "锁目录必须属于 root 且权限为 700。"
  exec 9<"${LOCK_DIR}"
  flock -n 9 || die "Another UTS instance is already running." "已有另一个 UTS 实例在运行。"
  WORKDIR="$(mktemp -d /tmp/uts.XXXXXX)" || die "Cannot create a private work directory." "无法创建私有工作目录。"
  trap cleanup EXIT
  trap 'interrupt 130' INT
  trap 'interrupt 143' TERM
}

require_root() {
  if [[ "${EUID}" -ne 0 ]]; then
    die "Please run this script as root." "请以 root 身份运行本脚本。"
  fi
}

detect_distro() {
  if [[ -r /etc/os-release ]]; then
    DISTRO_PRETTY="$(awk -F= '$1 == "PRETTY_NAME" { sub(/^[^=]*=/, ""); gsub(/^["\047]|["\047]$/, ""); print; exit }' /etc/os-release 2>/dev/null)" || DISTRO_PRETTY=""
  elif command_exists lsb_release; then
    DISTRO_PRETTY="$(lsb_release -ds 2>/dev/null)" || DISTRO_PRETTY=""
  elif [[ -r /etc/redhat-release ]]; then
    DISTRO_PRETTY="$(cat /etc/redhat-release 2>/dev/null)" || DISTRO_PRETTY=""
  fi
  DISTRO_PRETTY="${DISTRO_PRETTY#\"}"
  DISTRO_PRETTY="${DISTRO_PRETTY%\"}"
  DISTRO_PRETTY="${DISTRO_PRETTY:-unknown}"
}

parse_args() {
  local help=0
  while (($# > 0)); do
    case "$1" in
      -y|--yes) AUTO_YES=1 ;;
      --include-monitor) INCLUDE_MONITOR=1 ;;
      --dry-run) DRY_RUN=1 ;;
      --timeout)
        [[ $# -ge 2 && "$2" =~ ^[1-9][0-9]{0,3}$ && "$2" -le 3600 ]] \
          || die "--timeout requires 1 to 3600 seconds." "--timeout 需要 1 到 3600 之间的秒数。"
        COMMAND_TIMEOUT="$2"
        shift
        ;;
      -h|--help) help=1 ;;
      *) die "Unknown option: $1" "未知选项：$1" ;;
    esac
    shift
  done
  if [[ "${help}" -eq 1 ]]; then
    usage
    exit 0
  fi
}

component_name() {
  case "$1" in
    yunjing) msg "Host Security agent (YunJing)" "主机安全 Agent（YunJing）" ;;
    monitor) msg "Cloud Monitor (BaradAgent / Sgagent)" "云监控（BaradAgent / Sgagent）" ;;
  esac
}

component_selected() {
  case "$1" in
    yunjing) return 0 ;;
    monitor) [[ "${INCLUDE_MONITOR}" -eq 1 ]] ;;
  esac
}

confirm() {
  local reply=""
  [[ "${AUTO_YES}" -eq 0 && "${DRY_RUN}" -eq 0 ]] || return 0
  [[ -t 0 ]] || die "No terminal for confirmation. Add -y to run unattended." "没有可用于确认的终端，无人值守运行请加 -y。"
  msg "Continue? [y/N] " "确认卸载？[y/N] "
  printf '\n%s%s%s' "${C_BOLD}" "${MSG}" "${C_RESET}"
  read -r reply || reply=""
  case "${reply}" in
    y|Y|yes|Yes|YES) ;;
    *) die "Canceled; nothing was changed." "已取消，未做任何修改。" ;;
  esac
}

run_local_script() {
  local script_path="$1" description="" header="" rc=0
  msg "official $2${script_path}" "官方卸载程序 $2${script_path}"
  description="${MSG}"
  if [[ "${DRY_RUN}" -eq 1 ]]; then
    preview "would run ${description}" "将运行${description}"
    return 0
  fi
  [[ -s "${script_path}" && -x "${script_path}" ]] || { warn "Empty or non-executable uninstaller: ${description}." "卸载程序为空或不可执行：${description}。"; return 1; }
  IFS= read -r -d '' -n 4 header <"${script_path}" || true
  if [[ "${header}" != '#!'* && "${header}" != $'\177ELF' ]]; then
    warn "Unrecognized uninstaller format: ${description}." "无法识别的卸载程序格式：${description}。"
    return 1
  fi
  log "Running ${description}" "正在运行${description}"
  run sh -c 'cd -- "${1%/*}" && exec "$1"' uts "${script_path}" || rc=$?
  return "${rc}"
}

monitor_targets() {
  local dir=""
  printf '%s\n' "${STARGATE_DIRS[@]}"
  for dir in "${MONITOR_DIRS[@]}"; do
    printf '%s\n' "${dir}/barad"
  done
}

selected_dirs() {
  printf '%s\n' "${YUNJING_DIRS[@]}" "${YUNJING_RUNTIME[@]}"
  if [[ "${INCLUDE_MONITOR}" -eq 1 ]]; then
    monitor_targets
  fi
}

selected_leftovers() {
  printf '%s\n' "${YUNJING_FILES[@]}"
  if [[ "${INCLUDE_MONITOR}" -eq 1 ]]; then
    printf '%s\n' "${MONITOR_FILES[@]}"
  fi
}

path_present() {
  [[ -e "$1" || -L "$1" ]]
}

remove_path() {
  local path="$1" kind="${2:-file}"
  path_present "${path}" || return 0
  if [[ -d "${path}" && ! -L "${path}" ]]; then
    if [[ "${kind}" != tree ]]; then
      warn "Refusing directory at a file-only cleanup path: ${path}." "此清理路径只应是文件，拒绝删除目录：${path}。"
      return 1
    fi
    run rm -rf -- "${path}" || return $?
  else
    run rm -f -- "${path}" || return $?
  fi
  if [[ "${DRY_RUN}" -eq 0 ]] && path_present "${path}"; then
    warn "Path still exists: ${path}." "路径仍然存在：${path}。"
    return 1
  fi
}

remove_empty_dir() {
  local path="$1" entries=""
  [[ -d "${path}" && ! -L "${path}" ]] || return 0
  entries="$(find "${path}" -mindepth 1 -maxdepth 1 -print 2>/dev/null | head -n 1)" || return 0
  [[ -z "${entries}" ]] || return 0
  run rmdir -- "${path}" || return $?
}

path_in_dirs() {
  local executable="$1" dir=""
  shift
  for dir in "$@"; do
    [[ "${executable}" == "${dir}/"* ]] && return 0
  done
  return 1
}

resolved_path() {
  local base="$1" rest="" real="" part=""
  [[ "${base}" == /* ]] || return 1
  while [[ "${base}" != / && ! -e "${base}" && ! -L "${base}" ]]; do
    rest="/${base##*/}${rest}"
    base="${base%/*}"
    base="${base:-/}"
  done
  real="$(readlink -f -- "${base}" 2>/dev/null)" || return 1
  while [[ -n "${rest}" ]]; do
    rest="${rest#/}"
    part="${rest%%/*}"
    if [[ "${rest}" == */* ]]; then rest="${rest#*/}"; else rest=""; fi
    case "${part}" in
      ''|.) ;;
      ..) real="${real%/*}"; real="${real:-/}" ;;
      *) real="${real%/}/${part}" ;;
    esac
    if [[ -e "${real}" || -L "${real}" ]]; then
      real="$(readlink -f -- "${real}" 2>/dev/null)" || return 1
    fi
  done
  printf '%s\n' "${real}"
}

with_real_paths() {
  local dir="" real=""
  for dir in "$@"; do
    printf '%s\n' "${dir}"
    real="$(resolved_path "${dir}")" || continue
    [[ "${real}" == "${dir}" ]] || printf '%s\n' "${real}"
  done
}

process_token() {
  local entry="$1" started="" arguments=""
  started="$(awk '{ sub(/^.*\) /, ""); print $20 }' "${entry}/stat" 2>/dev/null)" || return 1
  [[ "${started}" =~ ^[0-9]+$ ]] || return 1
  arguments="$(cksum <"${entry}/cmdline" 2>/dev/null)" || return 1
  printf '%s|%s' "${started}" "${arguments}"
}

is_agent_process() {
  local entry="$1" executable="$2" script="" argv0="" cwd="" resolved=""
  shift 2
  path_in_dirs "${executable}" "$@" && return 0
  case "${executable##*/}" in
    sh|bash|dash|busybox)
      {
        IFS= read -r -d '' argv0 || return 1
        if [[ "${executable##*/}" == busybox ]]; then
          if [[ "${argv0##*/}" == busybox ]]; then
            IFS= read -r -d '' argv0 || return 1
          fi
          case "${argv0##*/}" in sh|ash|hush) ;; *) return 1 ;; esac
        fi
        while IFS= read -r -d '' script || [[ -n "${script}" ]]; do
          case "${script}" in
            --) IFS= read -r -d '' script || true; break ;;
            -?*|+?*) [[ "${script}" =~ ^[+-][abefhiklmnprtuvxBCEHPT]+$ ]] || return 1 ;;
            *) break ;;
          esac
        done
      } <"${entry}/cmdline" 2>/dev/null
      case "${script##*/}" in
        start.sh|stop.sh|restart.sh|trystart.sh|startYD.sh|stopYDCore.sh|YDCrontab.sh|self_update.sh|install.sh|init_install.sh)
          if [[ "${script}" != /* ]]; then
            cwd="$(readlink -- "${entry}/cwd" 2>/dev/null)" || {
              warn "Cannot inspect working directory for PID ${entry##*/}." "无法检查 PID ${entry##*/} 的工作目录。"
              return 2
            }
            script="${cwd% (deleted)}/${script}"
          fi
          resolved="$(resolved_path "${script}")" || {
            if path_in_dirs "${script}" "$@"; then
              warn "Cannot resolve agent script for PID ${entry##*/}: ${script}." "无法解析 PID ${entry##*/} 的 Agent 脚本：${script}。"
              return 2
            fi
            return 1
          }
          path_in_dirs "${resolved}" "$@" && return 0
          ;;
      esac
      ;;
  esac
  return 1
}

agent_processes() {
  local entry="" pid="" executable="" argv0="" token="" rc=0 match_rc=0
  local -a dirs=()
  [[ -r "${PROC_ROOT}/self/status" ]] || { warn "Cannot inspect /proc." "无法检查 /proc。"; return 1; }
  while IFS= read -r entry; do dirs+=("${entry}"); done < <(with_real_paths "$@")
  for entry in "${PROC_ROOT}"/[0-9]*; do
    [[ -d "${entry}" ]] || continue
    pid="${entry##*/}"
    [[ "${pid}" != "$$" ]] || continue
    if [[ ! -r "${entry}/cmdline" ]]; then
      if [[ -d "${entry}" ]]; then
        warn "Cannot inspect command line for PID ${pid}." "无法检查 PID ${pid} 的命令行。"
        rc=1
      fi
      continue
    fi
    argv0=""
    IFS= read -r -d '' argv0 <"${entry}/cmdline" 2>/dev/null || true
    [[ -n "${argv0}" ]] || continue
    executable="$(readlink -- "${entry}/exe" 2>/dev/null)" || {
      argv0=""
      IFS= read -r -d '' argv0 <"${entry}/cmdline" 2>/dev/null || true
      if [[ -n "${argv0}" ]]; then
        warn "Cannot inspect executable for PID ${pid}." "无法检查 PID ${pid} 的可执行文件。"
        rc=1
      fi
      continue
    }
    executable="${executable% (deleted)}"
    match_rc=0
    is_agent_process "${entry}" "${executable}" "${dirs[@]}" || match_rc=$?
    if [[ "${match_rc}" -ne 0 ]]; then
      [[ "${match_rc}" -eq 1 ]] || rc=1
      continue
    fi
    token="$(process_token "${entry}")" || {
      [[ ! -d "${entry}" ]] || rc=1
      continue
    }
    is_agent_process "${entry}" "${executable}" "${dirs[@]}" || match_rc=$?
    if [[ "${match_rc}" -ne 0 ]]; then
      [[ "${match_rc}" -eq 1 ]] || rc=1
      continue
    fi
    printf '%s|%s|%s\n' "${pid}" "${executable}" "${token}"
  done
  return "${rc}"
}

signal_agent_processes() {
  local signal="$1" targets="$2" pid="" expected="" current="" started="" arguments="" token=""
  while IFS='|' read -r pid expected started arguments; do
    [[ -n "${pid}" ]] || continue
    current="$(readlink -- "${PROC_ROOT}/${pid}/exe" 2>/dev/null)" || continue
    [[ "${current% (deleted)}" == "${expected}" ]] || continue
    token="$(process_token "${PROC_ROOT}/${pid}")" || continue
    [[ "${token}" == "${started}|${arguments}" ]] || continue
    if [[ "${DRY_RUN}" -eq 1 ]]; then
      printf '[DRY-RUN] kill -%s %s (%s)\n' "${signal}" "${pid}" "${expected}" >&3
    else
      kill -"${signal}" "${pid}" 2>/dev/null || warn "Cannot signal PID ${pid}: ${expected}." "无法向 PID ${pid} 发送信号：${expected}。"
    fi
  done <<<"${targets}"
}

show_processes() {
  local pid="" executable=""
  while IFS='|' read -r pid executable _; do
    printf '  PID %-8s %s\n' "${pid}" "${executable}" >&2
  done <<<"$1"
}

stop_agent_processes() {
  local targets="" signalled="" fresh=""
  targets="$(agent_processes "$@")" || return 1
  [[ -n "${targets}" ]] || return 0
  signal_agent_processes TERM "${targets}"
  [[ "${DRY_RUN}" -eq 0 ]] || return 0
  signalled="${targets}"
  for _ in 1 2 3 4 5; do
    sleep 1
    targets="$(agent_processes "$@")" || return 1
    [[ -n "${targets}" ]] || return 0
    fresh="$(grep -Fxv -f <(printf '%s\n' "${signalled}") <<<"${targets}")" || true
    [[ -z "${fresh}" ]] || signal_agent_processes TERM "${fresh}"
    signalled="${targets}"
  done
  warn "Agent processes remain; preserving their installation files:" "Agent 进程仍在运行，保留其安装文件："
  show_processes "${targets}"
  return 1
}

remove_paths() {
  local kind="$1" path="" rc=0
  shift
  for path in "$@"; do
    remove_path "${path}" "${kind}" || { rc=$?; stop_on "${rc}" && return "${rc}"; rc=1; }
  done
  return "${rc}"
}

guard_hint() {
  warn "In the Host Security console, turn off Agent uninstall protection or remove" "请在主机安全控制台关闭「Agent 卸载保护」，或将本机移出保护范围。"
  warn "this server from its scope, wait about a minute, then run this script again." "约一分钟后重新运行本脚本。"
}

uninstall_yunjing() {
  section "Host Security agent (YunJing)" "主机安全 Agent（YunJing）"
  local dir="" path="" rc=0 official_rc=0 ran=0
  for dir in "${YUNJING_DIRS[@]}"; do
    path_present "${dir}/uninst.sh" || continue
    ran=1
    run_local_script "${dir}/uninst.sh" "" || official_rc=$?
    break
  done
  [[ "${ran}" -eq 1 ]] || log "No official uninst.sh found; cleaning local remnants only." "未找到官方 uninst.sh，仅清理本地残留。"
  if [[ "${official_rc}" -ne 0 ]]; then
    warn "Official uninst.sh exited with status ${official_rc}." "官方 uninst.sh 退出码为 ${official_rc}。"
    warn "Local cleanup was not started. Resolve the official failure before retrying." "未开始本地清理，请先解决官方卸载失败的问题再重试。"
    return "${official_rc}"
  fi
  if ! stop_agent_processes "${YUNJING_DIRS[@]}"; then
    guard_hint
    return 6
  fi
  remove_paths tree "${YUNJING_DIRS[@]}" "${YUNJING_RUNTIME[@]}" || { rc=$?; stop_on "${rc}" && return "${rc}"; rc=1; }
  remove_paths file "${YUNJING_FILES[@]}" || { rc=$?; stop_on "${rc}" && return "${rc}"; rc=1; }
  [[ "${rc}" -eq 0 ]] || return 1
}

uninstall_monitor() {
  [[ "${INCLUDE_MONITOR}" -eq 1 ]] || return 0
  section "Cloud Monitor (BaradAgent / Sgagent)" "云监控（BaradAgent / Sgagent）"
  local dir="" path="" rc=0
  local -a removals=()
  while IFS= read -r path; do removals+=("${path}"); done < <(monitor_targets)
  for dir in "${MONITOR_DIRS[@]}"; do
    path="${dir}/barad/admin/uninstall.sh"
    path_present "${path}" || continue
    run_local_script "${path}" "BaradAgent " || return $?
  done
  for dir in "${STARGATE_DIRS[@]}"; do
    path="${dir}/admin/uninstall.sh"
    path_present "${path}" || continue
    run_local_script "${path}" "Sgagent " || return $?
  done
  stop_agent_processes "${removals[@]}" || return 1
  remove_paths tree "${removals[@]}" || { rc=$?; stop_on "${rc}" && return "${rc}"; rc=1; }
  for dir in "${MONITOR_DIRS[@]}"; do
    remove_empty_dir "${dir}" || { rc=$?; stop_on "${rc}" && return "${rc}"; rc=1; }
  done
  remove_paths file "${MONITOR_FILES[@]}" || { rc=$?; stop_on "${rc}" && return "${rc}"; rc=1; }
  return "${rc}"
}

verify_uninstall() {
  local path="" targets="" found=0
  local -a dirs=()
  while IFS= read -r path; do
    dirs+=("${path}")
    if path_present "${path}"; then
      warn "Installation path remains: ${path}." "安装路径仍然存在：${path}。"
      found=1
    fi
  done < <(selected_dirs)
  targets="$(agent_processes "${dirs[@]}")" || return 1
  if [[ -n "${targets}" ]]; then
    warn "Agent processes remain:" "仍有 Agent 进程："
    show_processes "${targets}"
    found=1
  fi
  while IFS= read -r path; do
    [[ -n "${path}" ]] || continue
    if path_present "${path}"; then
      warn "Leftover file remains: ${path}." "残留文件仍然存在：${path}。"
      found=1
    fi
  done < <(selected_leftovers)
  return "${found}"
}

finish() {
  local verify_rc="$1"
  printf '\n'
  if [[ "${DRY_RUN}" -eq 1 && "${FAILURES}" -eq 0 ]]; then
    ok "Dry run finished; nothing was changed." "预览完成，未做任何修改。"
  elif [[ "${DRY_RUN}" -eq 0 && "${FAILURES}" -eq 0 && "${verify_rc}" -eq 0 ]]; then
    ok "${SCRIPT_NAME}: local uninstall verified." "${SCRIPT_NAME}：本地卸载已验证。"
  else
    warn "${SCRIPT_NAME}: incomplete. See the failed operations and remaining items above." \
      "${SCRIPT_NAME}：未完成，请查看上方失败的操作和残留项。"
  fi
}

stop_on() {
  [[ "$1" -eq 124 || "$1" -ge 128 ]]
}

exit_status() {
  if [[ "$1" -eq 6 ]] || stop_on "$1"; then
    return "$1"
  fi
  return 1
}

main() {
  local verify_rc=0 rc=0 yunjing_rc=0

  exec 3>&1
  parse_args "$@"
  require_linux
  require_root
  detect_distro
  prepare_runtime
  log "System: ${DISTRO_PRETTY}, $(uname -m)" "系统：${DISTRO_PRETTY}，$(uname -m)"
  confirm

  uninstall_yunjing || yunjing_rc=$?
  if [[ "${yunjing_rc}" -ne 0 ]]; then
    component_name yunjing
    warn "${MSG} uninstall is incomplete (${yunjing_rc}); optional components were not processed." \
      "${MSG}卸载未完成（${yunjing_rc}），未处理可选组件。"
    FAILURES=1
    finish 1
    exit_status "${yunjing_rc}"
  fi

  if component_selected monitor; then
    uninstall_monitor || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
      component_name monitor
      record_failure "${MSG} uninstall is incomplete (${rc})." "${MSG}卸载未完成（${rc}）。"
      if stop_on "${rc}"; then
        finish 1
        return "${rc}"
      fi
    fi
  fi

  if [[ "${DRY_RUN}" -eq 0 ]]; then
    section "Verification" "验证"
    if verify_uninstall; then
      ok "No agent files or processes remain." "未发现 Agent 文件或进程残留。"
    else
      verify_rc=$?
    fi
  fi
  finish "${verify_rc}"
  if [[ "${FAILURES}" -eq 0 && "${verify_rc}" -eq 0 ]]; then
    return 0
  fi
  return 1
}

if [[ "${BASH_SOURCE[0]:-$0}" == "$0" ]]; then
  main "$@"
fi
