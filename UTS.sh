#!/usr/bin/env bash

set -euo pipefail

readonly SCRIPT_NAME="Uninstall-Tencent-Service"
readonly VERSION="1.0.0"
FAILURES=0
YUNJING_DIRS=(/usr/local/qcloud/YunJing /var/lib/qcloud/YunJing)
YUNJING_RUNTIME=(/run/ydeyes /var/run/ydeyes)
YUNJING_FILES=(/tmp/yunjing_install /tmp/yunjing_install_nohup)

AUTO_YES=0
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
  -h, --help               显示本帮助

脚本先运行主机上的官方卸载程序，再删除并验证剩余内容。
运行前请在主机安全控制台关闭「Agent 卸载保护」，或将本机移出保护范围。

退出码：
  0         卸载已验证
  1         有步骤失败、仍有残留，或运行被拒绝、取消
HELP
    return
  fi
  cat <<HELP
${SCRIPT_NAME} ${VERSION}
Remove Tencent Cloud preinstalled agents from a Linux server.

Usage: ./UTS.sh [options]

Options:
  -y, --yes                Skip the confirmation prompt (required without a terminal)
  -h, --help               Show this help message

Runs the official uninstaller found on the host, then removes and verifies the
remnants locally.
Disable Agent uninstall protection in the Host Security console before running.

Exit status:
  0         uninstall verified
  1         a step failed, remnants remain, or the run was refused or canceled
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

section() {
  msg "$@"
  printf '\n%s==>%s %s%s%s\n' "${C_BOLD}${C_BLUE}" "${C_RESET}" "${C_BOLD}" "${MSG}" "${C_RESET}"
}

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

run() {
  local rc=0 command=""
  "$@" </dev/null || rc=$?
  if [[ "${rc}" -ne 0 ]]; then
    command="$(printf ' %q' "$@")"
    warn "Command failed (${rc}):${command}" "命令失败（${rc}）：${command# }"
  fi
  return "${rc}"
}

require_linux() {
  if [[ "$(uname -s)" != Linux ]]; then
    die "Only Linux is supported. No uninstall was attempted." "仅支持 Linux，未执行任何卸载。"
  fi
}

prepare_runtime() {
  command_exists awk || die "Required command missing: awk. Install it before retrying." "缺少必需命令 awk，请安装后重试。"
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

confirm() {
  local reply=""
  [[ "${AUTO_YES}" -eq 0 ]] || return 0
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
  msg "official ${script_path}" "官方卸载程序 ${script_path}"
  description="${MSG}"
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

selected_dirs() {
  printf '%s\n' "${YUNJING_DIRS[@]}" "${YUNJING_RUNTIME[@]}"
}

selected_leftovers() {
  printf '%s\n' "${YUNJING_FILES[@]}"
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
  if path_present "${path}"; then
    warn "Path still exists: ${path}." "路径仍然存在：${path}。"
    return 1
  fi
}

remove_paths() {
  local kind="$1" path="" rc=0
  shift
  for path in "$@"; do
    remove_path "${path}" "${kind}" || rc=1
  done
  return "${rc}"
}

uninstall_yunjing() {
  section "Host Security agent (YunJing)" "主机安全 Agent（YunJing）"
  local dir="" path="" rc=0 official_rc=0 ran=0
  for dir in "${YUNJING_DIRS[@]}"; do
    path_present "${dir}/uninst.sh" || continue
    ran=1
    run_local_script "${dir}/uninst.sh" || official_rc=$?
    break
  done
  [[ "${ran}" -eq 1 ]] || log "No official uninst.sh found; cleaning local remnants only." "未找到官方 uninst.sh，仅清理本地残留。"
  if [[ "${official_rc}" -ne 0 ]]; then
    warn "Official uninst.sh exited with status ${official_rc}." "官方 uninst.sh 退出码为 ${official_rc}。"
    warn "Local cleanup was not started. Resolve the official failure before retrying." "未开始本地清理，请先解决官方卸载失败的问题再重试。"
    return "${official_rc}"
  fi
  remove_paths tree "${YUNJING_DIRS[@]}" "${YUNJING_RUNTIME[@]}" || rc=1
  remove_paths file "${YUNJING_FILES[@]}" || rc=1
  [[ "${rc}" -eq 0 ]] || return 1
}

verify_uninstall() {
  local path="" found=0
  while IFS= read -r path; do
    if path_present "${path}"; then
      warn "Installation path remains: ${path}." "安装路径仍然存在：${path}。"
      found=1
    fi
  done < <(selected_dirs)
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
  if [[ "${FAILURES}" -eq 0 && "${verify_rc}" -eq 0 ]]; then
    ok "${SCRIPT_NAME}: local uninstall verified." "${SCRIPT_NAME}：本地卸载已验证。"
  else
    warn "${SCRIPT_NAME}: incomplete. See the failed operations and remaining items above." \
      "${SCRIPT_NAME}：未完成，请查看上方失败的操作和残留项。"
  fi
}

main() {
  local verify_rc=0 yunjing_rc=0

  parse_args "$@"
  require_linux
  require_root
  detect_distro
  prepare_runtime
  log "System: ${DISTRO_PRETTY}, $(uname -m)" "系统：${DISTRO_PRETTY}，$(uname -m)"
  confirm

  uninstall_yunjing || yunjing_rc=$?
  if [[ "${yunjing_rc}" -ne 0 ]]; then
    warn "Host Security agent (YunJing) uninstall is incomplete (${yunjing_rc})." "主机安全 Agent（YunJing）卸载未完成（${yunjing_rc}）。"
    FAILURES=1
    finish 1
    return 1
  fi

  section "Verification" "验证"
  if verify_uninstall; then
    ok "No agent files remain." "未发现 Agent 文件残留。"
  else
    verify_rc=$?
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
