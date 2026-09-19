#!/usr/bin/env bash
# ============================================================================
# WireGuard 组网 + 流量监控 一键部署引导脚本
#
# 用法（在目标服务器上以 root 执行）：
#   curl -fsSL -o /root/deploy.sh https://raw.githubusercontent.com/Ma6302/wireguard-setup-scripts/main/deploy.sh \
#     && bash /root/deploy.sh
#
# 可选参数：
#   --dry-run        只下载到临时目录并校验，不修改服务器任何文件
#   --sendkey=KEY    非交互指定 Server酱 SendKey（不填则安装 wgmon 时提示输入）
#   --no-wg          跳过 wg.sh（WireGuard 已装好时用）
#   --ref=REF        指定 git ref（分支/标签）。默认自动解析为最新的 vX.Y.Z Release 标签
#                    （整仓不可变快照，即你发布过的验证版本）；加 --ref=main 可跟随分支最新提交
#
# 说明：本脚本只做「下载 → 校验 → 备份 → 放置」，不会替你修改隧道配置；
#       WireGuard 的实际安装由 wg.sh 的交互菜单完成（需要你选端口、DNS、首个客户端名）。
# ============================================================================
set -uo pipefail

REPO=Ma6302/wireguard-setup-scripts
REF=""
REF_SRC=""
WG_SH=/root/wg.sh
WGMON_DIR=/opt/wgmon
TS=$(date +%Y%m%d-%H%M%S)

DRY_RUN=0
SENDKEY=""
SKIP_WG=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-wg)   SKIP_WG=1 ;;
    --sendkey=*) SENDKEY="${arg#--sendkey=}" ;;
    --ref=*)     REF="${arg#--ref=}"; REF_SRC="命令行指定" ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

say()  { echo -e "\n\033[1;36m== $* ==\033[0m"; }
ok()   { echo -e "  \033[32m✓\033[0m $*"; }
warn() { echo -e "  \033[33m!\033[0m $*"; }
die()  { echo -e "\n\033[31m✗ $*\033[0m"; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行"
command -v curl >/dev/null || die "未找到 curl（apt install -y curl）"

resolve_latest_ref() {
  # 与 wgmon 自更新同策略：先 Releases（草稿不计），再 Tags，
  # 识别 vX.Y.Z（推荐）与兼容 wgmon-vX.Y.Z，按数字取最高（v1.10.0 > v1.9.9）
  local kind body best
  for kind in releases tags; do
    body=$(curl -fsSL -m 15 "https://api.github.com/repos/$REPO/$kind?per_page=100" 2>/dev/null) || continue
    best=$(printf '%s' "$body" \
      | grep -oE '"(tag_name|name)": *"(wgmon-)?v[0-9]+(\.[0-9]+)*"' \
      | grep -oE '(wgmon-)?v[0-9]+(\.[0-9]+)*' \
      | awk '{ v=$0; sub(/^wgmon-/,"",v); sub(/^v/,"",v); print v" "$0 }' \
      | sort -V -k1,1 | tail -1 | awk '{ print $2 }')
    if [[ -n "$best" ]]; then printf '%s' "$best"; return 0; fi
  done
  return 1
}

if [[ -z "$REF" ]]; then
  if REF=$(resolve_latest_ref) && [[ -n "$REF" ]]; then
    REF_SRC="最新 Release 标签（不可变快照）"
  else
    REF=main
    REF_SRC="main 分支（未找到 vX.Y.Z 标签或 API 不可达）"
  fi
fi

fetch() {  # $1=仓库内相对路径  $2=输出文件（raw 主通道 + jsdelivr 兜底，锁定同一 ref）
  local rel="$1" out="$2"
  if curl -fsSL -m 25 "https://raw.githubusercontent.com/$REPO/$REF/$rel" -o "$out"; then
    echo "https://raw.githubusercontent.com/$REPO/$REF" >/tmp/.wg_fetch_src
    return 0
  fi
  if curl -fsSL -m 25 "https://cdn.jsdelivr.net/gh/$REPO@$REF/$rel" -o "$out"; then
    echo "https://cdn.jsdelivr.net/gh/$REPO@$REF" >/tmp/.wg_fetch_src
    return 0
  fi
  return 1
}

say "步骤 1/3  下载脚本（ref=$REF → $REF_SRC；raw 主通道，jsdelivr 兜底）"
WORK=$(mktemp -d /tmp/wgdeploy.XXXXXX)
fetch "wg.sh" "$WORK/wg.sh" || die "下载 wg.sh 失败（两路均不可达，请检查网络）"
ok "wg.sh 下载完成（源: $(cat /tmp/.wg_fetch_src)）"
for rel in "wg-traffic-monitor/wgmon.py" "wg-traffic-monitor/install.sh" "wg-traffic-monitor/config.example.ini"; do
  fetch "$rel" "$WORK/$(basename "$rel")" || die "下载 $rel 失败"
  ok "$(basename "$rel") 下载完成"
done
mkdir -p "$WORK/wg-traffic-monitor"
mv "$WORK"/wgmon.py "$WORK"/install.sh "$WORK"/config.example.ini "$WORK/wg-traffic-monitor/"
bash -n "$WORK/wg.sh" || die "wg.sh 语法校验失败（下载损坏？）"
ok "wg.sh 语法校验通过（$(wc -l < "$WORK/wg.sh") 行, md5 $(md5sum "$WORK/wg.sh" | cut -c1-8)）"

if [[ $DRY_RUN -eq 1 ]]; then
  say "dry-run 结束：仅下载并校验，服务器文件未做任何改动"
  echo "  临时目录: $WORK（可自行删除）"
  exit 0
fi

say "步骤 2/3  放置脚本并（按需）安装 WireGuard"
if [[ -f "$WG_SH" ]]; then
  if [[ "$(md5sum "$WORK/wg.sh" | cut -d' ' -f1)" == "$(md5sum "$WG_SH" | cut -d' ' -f1)" ]]; then
    ok "已存在 /root/wg.sh 且与仓库版本相同，跳过"
  else
    cp -a "$WG_SH" "$WG_SH.bak-deploy-$TS" || die "备份 /root/wg.sh 失败"
    install -m 700 "$WORK/wg.sh" "$WG_SH" || die "替换 /root/wg.sh 失败"
    ok "wg.sh 已更新（旧版备份 /root/wg.sh.bak-deploy-$TS）"
  fi
else
  install -m 700 "$WORK/wg.sh" "$WG_SH" || die "安装 /root/wg.sh 失败"
  ok "wg.sh 已安装到 $WG_SH"
fi

if command -v wg >/dev/null; then
  ok "检测到 WireGuard 已安装（$(wg --version 2>/dev/null || echo wg)），跳过安装"
elif [[ $SKIP_WG -eq 1 ]]; then
  warn "未检测到 wg 命令，但指定了 --no-wg，跳过"
else
  warn "未检测到 WireGuard，接下来运行 wg.sh 交互安装（按提示操作，装完选退出）"
  read -rp "  回车开始运行 wg.sh（Ctrl+C 中止）..." || true
  bash "$WG_SH" || warn "wg.sh 退出码非 0，请检查上方输出"
  command -v wg >/dev/null || die "仍未检测到 wg 命令，wgmon 无法安装；请先完成 WireGuard 安装后重跑本脚本"
fi

say "步骤 3/3  安装 wgmon 流量监控"
[[ -d "$WGMON_DIR" ]] || mkdir -p "$WGMON_DIR"
if [[ -f "$WGMON_DIR/wgmon.py" ]]; then
  cp -a "$WGMON_DIR/wgmon.py" "$WGMON_DIR/wgmon.py.bak-deploy-$TS" || die "备份 wgmon.py 失败"
  ok "已有 wgmon 备份为 wgmon.py.bak-deploy-$TS"
fi
install -m 700 "$WORK/wg-traffic-monitor/wgmon.py" "$WGMON_DIR/wgmon.py" || die "安装 wgmon.py 失败"

if [[ -f "$WGMON_DIR/config.ini" ]]; then
  ok "检测到已有 config.ini，保留现有配置（SendKey/阈值/时间不变）"
else
  key="$SENDKEY"
  if [[ -z "$key" ]]; then
    echo "  Server酱 SendKey 获取：微信扫码登录 https://sct.ftqq.com （免费）"
    read -rp "  粘贴 SendKey（直接回车 = 稍后手动填）: " key || true
  fi
  sed "s|^sendkey =.*|sendkey = ${key}|" "$WORK/wg-traffic-monitor/config.example.ini" > "$WGMON_DIR/config.ini"
  chmod 600 "$WGMON_DIR/config.ini"
  if [[ -n "$key" ]]; then ok "SendKey 已写入 config.ini"; else warn "SendKey 暂空，稍后执行 'wgmon menu' 的 7) 填写"; fi
fi

python3 -m py_compile "$WGMON_DIR/wgmon.py" || die "wgmon.py 语法校验失败"
ok "wgmon.py 语法校验通过"
python3 "$WGMON_DIR/wgmon.py" install-cron || die "安装定时任务失败"
ok "定时任务已安装（快照 + 每晚 23:30 日报）"
ln -sf "$WGMON_DIR/wgmon.py" /usr/local/bin/wgmon || warn "创建快捷命令失败，可直接用 python3 $WGMON_DIR/wgmon.py menu"
ok "快捷命令已就绪：wgmon"

say "部署完成"
echo "  WireGuard 管理菜单 : bash $WG_SH"
echo "  wgmon 交互菜单     : wgmon menu      （查用量/改阈值/改时间/改 SendKey）"
echo "  立即验证推送       : wgmon selftest  （需先填好 SendKey）"
echo "  本次临时目录       : $WORK（可删除：rm -rf $WORK）"
