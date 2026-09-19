#!/usr/bin/env bash
# wgmon 一键部署脚本（在服务器上以 root 执行）
# 用法：cd wg-traffic-monitor && bash install.sh
# 作用：安装到 /opt/wgmon，初始化配置与数据库，发测试消息，装定时任务
# 说明：已存在 /opt/wgmon/config.ini 时不会覆盖（保护 SendKey 与自定义设置）
set -euo pipefail

DEST=/opt/wgmon
SRC="$(cd "$(dirname "$0")" && pwd)"

[[ $EUID -eq 0 ]] || { echo "请用 root 运行"; exit 1; }
command -v wg >/dev/null || { echo "未找到 wg 命令"; exit 1; }
command -v python3 >/dev/null || { echo "未找到 python3"; exit 1; }

mkdir -p "$DEST"
install -m 700 "$SRC/wgmon.py" "$DEST/wgmon.py"

if [[ -f "$DEST/config.ini" ]]; then
  echo "已存在 $DEST/config.ini，保留现有配置"
elif [[ -f "$SRC/config.ini" ]]; then
  install -m 600 "$SRC/config.ini" "$DEST/config.ini"
elif [[ -f "$SRC/config.example.ini" ]]; then
  install -m 600 "$SRC/config.example.ini" "$DEST/config.ini"
  echo "已从 config.example.ini 生成配置：请编辑 $DEST/config.ini 填入 SendKey 后再自检"
fi

python3 -m py_compile "$DEST/wgmon.py"
echo "[1/3] 文件已安装到 $DEST 并通过语法检查"

python3 "$DEST/wgmon.py" selftest
echo "[2/3] 自检完成（上方应显示 推送测试: 成功；若失败请先填 SendKey）"

python3 "$DEST/wgmon.py" install-cron
echo "[3/3] 定时任务已安装"
echo "完成。快捷命令：ln -sf $DEST/wgmon.py /usr/local/bin/wgmon ；菜单：wgmon menu"
