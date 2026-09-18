#!/usr/bin/env bash
# ============================================================
# SSH 加固脚本（幂等 / 可回滚）
# 作者：Ma6302（https://github.com/Ma6302）
# 适用：阿里云轻量 Ubuntu 22.04.5 LTS / OpenSSH 8.9p1
# 效果：root 仅允许密钥登录，密码登录彻底关闭
# 回滚：rm -f /etc/ssh/sshd_config.d/99-hardening.conf && systemctl reload ssh
#
# 用法：bash harden-ssh.sh          # 加固
#      bash harden-ssh.sh --check # 只看当前状态，不做任何改动
# ============================================================
set -euo pipefail

DROPIN="/etc/ssh/sshd_config.d/99-hardening.conf"
TS="$(date +%Y%m%d-%H%M%S)"
BACKUP="/root/ssh-hardening-backup-$TS"

die() { echo "❌ $*" >&2; exit 1; }

show_state() {
  echo "-- 当前 sshd 实际取值 --"
  sshd -T | grep -E '^(permitrootlogin|passwordauthentication|pubkeyauthentication|kbdinteractiveauthentication|maxauthtries|logingracetime|x11forwarding|allowagentforwarding)'
}

# ---------- --check：只读模式 ----------
if [ "${1:-}" = "--check" ]; then
  show_state
  echo
  echo "-- root 公钥数量 --"
  grep -cE '^(ssh-|ecdsa-|sk-)' /root/.ssh/authorized_keys 2>/dev/null || echo 0
  echo
  echo "-- drop-in 是否存在 --"
  ls -la "$DROPIN" 2>/dev/null || echo "（不存在，当前为未加固状态）"
  exit 0
fi

# ---------- 0. 前置条件 ----------
[ "$(id -u)" -eq 0 ] || die "请以 root 执行"

echo "== 0. 前置检查 =="

grep -qE '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/\*\.conf' /etc/ssh/sshd_config \
  || die "主配置缺少 sshd_config.d 的 Include，drop-in 方案不适用，请先人工确认后再加固"

KEYN=$(grep -cE '^(ssh-|ecdsa-|sk-)' /root/.ssh/authorized_keys 2>/dev/null || true)
echo "root authorized_keys 公钥数: ${KEYN:-0}"
[ "${KEYN:-0}" -ge 1 ] || die "未检测到任何公钥。此时关闭密码登录会把你锁在门外，已中止"

show_state

# ---------- 1. 备份 ----------
echo
echo "== 1. 备份现有配置 =="
mkdir -p "$BACKUP"
cp -a /etc/ssh/sshd_config "$BACKUP/sshd_config.bak"
if [ -d /etc/ssh/sshd_config.d ]; then
  cp -a /etc/ssh/sshd_config.d "$BACKUP/sshd_config.d.bak" || true
fi
echo "备份目录: $BACKUP"

# ---------- 2. 写入 drop-in ----------
echo
echo "== 2. 写入加固配置 =="
mkdir -p /etc/ssh/sshd_config.d
cat > "$DROPIN" <<'CONF'
# WorkBuddy SSH 加固配置（幂等；整体删除本文件即可回滚）
# 本文件经主配置第 12 行 Include 提前加载。sshd 对多数指令取「先出现者为准」，
# 因此以下取值会覆盖主配置后部的同名字段（如第 125/126 行的 PermitRootLogin yes）。
PermitRootLogin prohibit-password
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
PermitEmptyPasswords no
MaxAuthTries 3
LoginGraceTime 30
X11Forwarding no
AllowAgentForwarding no
ClientAliveInterval 300
ClientAliveCountMax 2
CONF
echo "已写入 $DROPIN"

# ---------- 3. 语法校验（失败自动回滚） ----------
echo
echo "== 3. 语法校验 =="
if ! sshd -t; then
  rm -f "$DROPIN"
  die "sshd 配置校验失败，已移除 drop-in 并中止（现有配置未受影响）"
fi
echo "✅ 语法校验通过"

# ---------- 4. 生效 ----------
echo
echo "== 4. reload（不断开现有连接） =="
systemctl reload ssh
sleep 1

echo
echo "== 5. 生效后的实际取值 =="
show_state

echo
echo "== 6. 登录自检（重要，别跳过） =="
echo "  保持当前会话不要关闭，另开一个终端执行："
echo "    ssh -i <你的私钥> root@<SERVER_IP> 'echo OK'"
echo "  看到 OK 后再关闭旧会话。"
echo "  若失败：用阿里云控制台「远程连接」(VNC) 进入，执行回滚："
echo "    rm -f $DROPIN && systemctl reload ssh"
echo
echo "✅ 加固完成。回滚命令： rm -f $DROPIN && systemctl reload ssh"
