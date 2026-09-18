#!/usr/bin/env bash
# ============================================================
# WireGuard 出方向防护（幂等 / 可回滚）
# 作者：Ma6302（https://github.com/Ma6302）
#
# 背景：本机作为 WireGuard 全隧道的 NAT 出口，客户端所有出站流量
#       源 IP 都被 MASQUERADE 改写为服务器自身。任一客户端设备若
#       出现端口扫描 / P2P 狂发等行为，云平台看到的就是「服务器在
#       对外攻击」，会触发出方向阻断（ALL:ALL）。
#
# 作用：在不影响正常上网的前提下，抑制这类异常连接行为。
#
# 用法： wg-forward-guard.sh on      启用
#       wg-forward-guard.sh off     清除全部规则
#       wg-forward-guard.sh status  查看现状
#
# 回滚： systemctl disable --now wg-forward-guard
#        rm -f /etc/systemd/system/wg-forward-guard.service
#        rm -f /usr/local/sbin/wg-forward-guard.sh
#        systemctl daemon-reload
# ============================================================
set -euo pipefail

WG_NET="10.7.0.0/24"
WAN_IF="eth0"

# 出方向高危端口：扫描器与蠕虫最爱，正常上网几乎不会访问。
# 有意不含 22（不影响从客户端 SSH 到别的机器）与 8080（常见网站端口）。
PORTS="23 135 137 138 139 445 1433 1521 2323 3306 3389 5432 5555 5900 6379 7547 8291 11211 27017"

# 阈值：正常浏览/看视频单设备新建 TCP < 10/秒；端口扫描是几百~几千/秒。
# 40/秒留出 4 倍余量，使用体感不到，扫描会被削平。
TCP_RATE="40/sec"
TCP_BURST="80"
UDP_RATE="500/sec"
UDP_BURST="1000"
HTABLE_EXPIRE="20000"

IPT="/usr/sbin/iptables"
[ -x "$IPT" ] || IPT="$(command -v iptables)"

add_rule() {
  if "$IPT" -C FORWARD "$@" 2>/dev/null; then
    return 0
  fi
  "$IPT" -I FORWARD 1 "$@"
}

del_rule() {
  "$IPT" -D FORWARD "$@" 2>/dev/null || true
}

case "${1:-}" in
  on)
    echo "== 启用出方向防护 =="

    for p in $PORTS; do
      add_rule -s "$WG_NET" -o "$WAN_IF" -p tcp --dport "$p" -j DROP
    done
    echo "  [1/3] 已阻断出方向高危端口（$(echo $PORTS | wc -w) 个）"

    add_rule -s "$WG_NET" -o "$WAN_IF" -p tcp --syn \
      -m hashlimit --hashlimit-above "$TCP_RATE" --hashlimit-burst "$TCP_BURST" \
      --hashlimit-mode srcip --hashlimit-name wg_tcp_new \
      --hashlimit-htable-expire "$HTABLE_EXPIRE" -j DROP
    echo "  [2/3] 已限制单设备 TCP 新建连接速率：$TCP_RATE（突发 $TCP_BURST）"

    add_rule -s "$WG_NET" -o "$WAN_IF" -p udp \
      -m hashlimit --hashlimit-above "$UDP_RATE" --hashlimit-burst "$UDP_BURST" \
      --hashlimit-mode srcip --hashlimit-name wg_udp \
      --hashlimit-htable-expire "$HTABLE_EXPIRE" -j DROP
    echo "  [3/3] 已限制单设备 UDP 速率：$UDP_RATE（突发 $UDP_BURST）"

    echo "== 完成 =="
    ;;

  off)
    echo "== 关闭出方向防护 =="

    for p in $PORTS; do
      del_rule -s "$WG_NET" -o "$WAN_IF" -p tcp --dport "$p" -j DROP
    done

    del_rule -s "$WG_NET" -o "$WAN_IF" -p tcp --syn \
      -m hashlimit --hashlimit-above "$TCP_RATE" --hashlimit-burst "$TCP_BURST" \
      --hashlimit-mode srcip --hashlimit-name wg_tcp_new \
      --hashlimit-htable-expire "$HTABLE_EXPIRE" -j DROP

    del_rule -s "$WG_NET" -o "$WAN_IF" -p udp \
      -m hashlimit --hashlimit-above "$UDP_RATE" --hashlimit-burst "$UDP_BURST" \
      --hashlimit-mode srcip --hashlimit-name wg_udp \
      --hashlimit-htable-expire "$HTABLE_EXPIRE" -j DROP

    echo "== 已清除 =="
    ;;

  status)
    echo "== FORWARD 链规则 =="
    "$IPT" -S FORWARD
    echo
    echo "== 计数器 =="
    "$IPT" -L FORWARD -v -n
    ;;

  *)
    echo "用法: $0 {on|off|status}" >&2
    exit 1
    ;;
esac
