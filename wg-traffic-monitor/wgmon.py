#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wgmon —— WireGuard 流量监控 + 微信日报（零成本方案）

功能：
  1. 定时快照（cron 每 10 分钟）：解析 wg show all dump，增量记账到 SQLite
  2. 当日累计流量达到阈值时推送告警（每天最多 1 次，跨天自动重置）
  3. 每日固定时间推送流量日报到微信（Server酱·「方糖」服务号，免费）
  4. 自动更新：每天日报时比对 GitHub 仓库版本号，有新版自动下载安装（可在菜单开关）
  5. 交互菜单：查看用量 / 立即推送 / 修改阈值 / 修改推送时间 / 修改 SendKey / 卸载 / 更新

子命令：
  snapshot     采集一次快照并检查阈值（cron 用）
  daily        生成并推送当日流量日报（cron 用）
  selftest     初始化数据库 + 发送测试消息（部署验收用）
  status       打印当日/本月用量（只读）
  report       只生成日报文本不推送（调试用）
  install-cron 安装/刷新定时任务（按服务器时区自动换算）
  uninstall    卸载：移除定时任务与快捷命令（是否删脚本/数据交互确认）
  update       更新器：上传 wgmon.py.new / wg.sh.new 后执行，校验+备份+原子替换
  check-update 从 GitHub 检查并安装新版（版本号比较）
  version      打印当前版本号
  menu         交互菜单（需要终端）

依赖：仅 Python3 标准库。不修改 wg0.conf / wg.sh / 任何系统服务，纯只读监控。
"""

import configparser
import datetime
import glob
import os
import py_compile
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

# realpath 解析软链接（/usr/local/bin/wgmon -> /opt/wgmon/wgmon.py），
# 保证 config/db 始终落在真实安装目录
BASE_DIR = os.path.dirname(os.path.realpath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.ini")
DB_PATH = os.path.join(BASE_DIR, "wgmon.db")
CRON_TAG = "wgmon.py"  # crontab 幂等标记

# 版本号（与仓库 wg-traffic-monitor/VERSION 比较，决定是否自动更新）
VERSION = "1.1.0"

# 更新源：GitHub raw 为主，jsdelivr 镜像兜底（阿里云北京两个通道均实测可达）
DEFAULT_REPO_BASE = "https://raw.githubusercontent.com/Ma6302/wireguard-setup-scripts/main"
DEFAULT_MIRROR_BASE = "https://cdn.jsdelivr.net/gh/Ma6302/wireguard-setup-scripts@main"

WG_DUMP_CMD = ["wg", "show", "all", "dump"]
CLIENT_CONF_DIR = "/root"  # /root/<设备名>.conf


# ---------------------------------------------------------------- 配置

DEFAULT_CONFIG = """[notify]
provider = serverchan
# 在 sct.ftqq.com 用微信扫码登录后获取你的 SendKey，填到这里
sendkey = 

[traffic]
daily_threshold_gb = 20

[report]
utc_offset = 8
report_hour = 23
report_minute = 30

[peers]
# 可选手动映射（IP = 设备名）。留空则自动解析 /root/*.conf 文件名

[update]
# 自动更新：开启后每天日报时检查 GitHub 上的新版并自动安装（仅你的仓库，版本经你验证）
auto_update = true
repo_base = https://raw.githubusercontent.com/Ma6302/wireguard-setup-scripts/main
mirror_base = https://cdn.jsdelivr.net/gh/Ma6302/wireguard-setup-scripts@main
"""


def load_config():
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG)
        os.chmod(CONFIG_PATH, 0o600)
    cp = configparser.ConfigParser()
    cp.read(CONFIG_PATH, encoding="utf-8")

    class Cfg:
        provider = cp.get("notify", "provider", fallback="serverchan").strip()
        sendkey = cp.get("notify", "sendkey", fallback="").strip()
        threshold_gb = cp.getfloat("traffic", "daily_threshold_gb", fallback=0.0)
        snapshot_interval_min = max(1, cp.getint("traffic", "snapshot_interval_min", fallback=30))
        utc_offset = cp.getint("report", "utc_offset", fallback=8)
        report_hour = cp.getint("report", "report_hour", fallback=23)
        report_minute = cp.getint("report", "report_minute", fallback=30)
        peer_overrides = dict(cp.items("peers")) if cp.has_section("peers") else {}
        auto_update = cp.getboolean("update", "auto_update", fallback=True)
        repo_base = cp.get("update", "repo_base", fallback=DEFAULT_REPO_BASE).strip()
        mirror_base = cp.get("update", "mirror_base", fallback=DEFAULT_MIRROR_BASE).strip()

    return Cfg()


def save_config(cfg):
    """按当前配置重写 config.ini（菜单修改后调用）"""
    lines = [
        "[notify]",
        "provider = %s" % cfg.provider,
        "sendkey = %s" % cfg.sendkey,
        "",
        "[traffic]",
        "daily_threshold_gb = %g" % cfg.threshold_gb,
        "snapshot_interval_min = %d" % cfg.snapshot_interval_min,
        "",
        "[report]",
        "utc_offset = %d" % cfg.utc_offset,
        "report_hour = %d" % cfg.report_hour,
        "report_minute = %d" % cfg.report_minute,
        "",
        "[peers]",
        "# 可选手动映射（IP = 设备名）。留空则自动解析 /root/*.conf 文件名",
    ]
    for k, v in cfg.peer_overrides.items():
        lines.append("%s = %s" % (k, v))
    lines += [
        "",
        "[update]",
        "# 自动更新：开启后每天日报时检查 GitHub 上的新版并自动安装",
        "auto_update = %s" % ("true" if cfg.auto_update else "false"),
        "repo_base = %s" % cfg.repo_base,
        "mirror_base = %s" % cfg.mirror_base,
    ]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    os.chmod(CONFIG_PATH, 0o600)


# ---------------------------------------------------------------- 时间与单位

def now_bj(cfg):
    """北京时间（固定偏移，与服务器本地时区解耦；中国无夏令时）"""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=cfg.utc_offset)


def today_str(cfg):
    return now_bj(cfg).strftime("%Y-%m-%d")


def month_prefix(cfg):
    return now_bj(cfg).strftime("%Y-%m")


def human(n):
    """字节数转人类可读"""
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return ("%.2f %s" % (f, unit)) if unit != "B" else ("%d B" % n)
        f /= 1024


# ---------------------------------------------------------------- 数据库

def open_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS peer_daily (
        date TEXT NOT NULL, pubkey TEXT NOT NULL,
        rx INTEGER NOT NULL DEFAULT 0, tx INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (date, pubkey))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS snapshot_last (
        pubkey TEXT PRIMARY KEY,
        last_rx INTEGER NOT NULL, last_tx INTEGER NOT NULL,
        last_ts INTEGER NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS alert_state (
        date TEXT PRIMARY KEY, alerted INTEGER NOT NULL DEFAULT 0)""")
    conn.commit()
    return conn


# ---------------------------------------------------------------- wg 采集

def get_wg_peers():
    """返回 [{'pubkey','ip','rx','tx'}]。rx=服务端收到(设备上传)，tx=服务端发出(设备下载)"""
    try:
        out = subprocess.check_output(WG_DUMP_CMD, text=True, timeout=15)
    except Exception as e:
        raise RuntimeError("执行 wg show all dump 失败: %s" % e)
    peers = []
    for line in out.splitlines():
        f = line.split("\t")
        if len(f) != 9:
            # 接口行 5 列；peer 行 9 列（all dump 实测格式：
            # 接口名/公钥/预共享密钥/endpoint/allowed-ips/最近握手/rx/tx/keepalive）
            continue
        pubkey, allowed, rx, tx = f[1], f[4], int(f[6]), int(f[7])
        ip = ""
        for part in allowed.split(","):
            part = part.strip()
            if "." in part:  # 取第一个 IPv4
                ip = part.split("/")[0]
                break
        peers.append({"pubkey": pubkey, "ip": ip, "rx": rx, "tx": tx})
    return peers


def auto_name_map():
    """解析 /root/<设备名>.conf 里的 Address = 10.7.0.x，建立 IP->设备名 映射"""
    m = {}
    for path in sorted(glob.glob(os.path.join(CLIENT_CONF_DIR, "*.conf"))):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                text = f.read(4096)
        except OSError:
            continue
        mm = re.search(r"^\s*Address\s*=\s*([\d.]+)", text, re.M)
        if mm:
            m[mm.group(1)] = name
    return m


def peer_display(peer, cfg, auto_map):
    ip = peer["ip"]
    if ip and ip in cfg.peer_overrides:
        return cfg.peer_overrides[ip]
    if ip and ip in auto_map:
        return auto_map[ip]
    return ip or peer["pubkey"][:8]


# ---------------------------------------------------------------- 快照与阈值

def snapshot(cfg, verbose=False):
    peers = get_wg_peers()
    d = today_str(cfg)
    ts = int(datetime.datetime.utcnow().timestamp())
    conn = open_db()
    try:
        for p in peers:
            row = conn.execute(
                "SELECT last_rx, last_tx FROM snapshot_last WHERE pubkey=?",
                (p["pubkey"],)).fetchone()
            if row is None:  # 首次见到该 peer：只建基线，不计增量
                d_rx = d_tx = 0
            else:
                prev_rx, prev_tx = row
                if p["rx"] < prev_rx or p["tx"] < prev_tx:
                    # 计数器清零（wg/VPS 重启）：增量 = 新值
                    d_rx, d_tx = p["rx"], p["tx"]
                else:
                    d_rx, d_tx = p["rx"] - prev_rx, p["tx"] - prev_tx
            conn.execute(
                "INSERT INTO peer_daily(date,pubkey,rx,tx) VALUES(?,?,?,?) "
                "ON CONFLICT(date,pubkey) DO UPDATE SET rx=rx+?, tx=tx+?",
                (d, p["pubkey"], d_rx, d_tx, d_rx, d_tx))
            conn.execute(
                "INSERT INTO snapshot_last(pubkey,last_rx,last_tx,last_ts) VALUES(?,?,?,?) "
                "ON CONFLICT(pubkey) DO UPDATE SET last_rx=?, last_tx=?, last_ts=?",
                (p["pubkey"], p["rx"], p["tx"], ts, p["rx"], p["tx"], ts))
        conn.commit()
        if verbose:
            print("snapshot OK: %d peers, date=%s" % (len(peers), d))
    finally:
        conn.close()
    check_threshold(cfg, d)


def day_total(conn, d):
    row = conn.execute(
        "SELECT COALESCE(SUM(rx),0), COALESCE(SUM(tx),0) FROM peer_daily WHERE date=?",
        (d,)).fetchone()
    return row[0], row[1]


def month_total(conn, mprefix):
    row = conn.execute(
        "SELECT COALESCE(SUM(rx),0), COALESCE(SUM(tx),0) "
        "FROM peer_daily WHERE date LIKE ?", (mprefix + "-%",)).fetchone()
    return row[0], row[1]


def check_threshold(cfg, d):
    """当日总量达到阈值则告警一次/天"""
    if cfg.threshold_gb <= 0:
        return
    threshold = int(cfg.threshold_gb * 1024 ** 3)
    conn = open_db()
    try:
        rx, tx = day_total(conn, d)
        if rx + tx < threshold:
            return
        row = conn.execute(
            "SELECT alerted FROM alert_state WHERE date=?", (d,)).fetchone()
        if row and row[0]:
            return
        # 生成告警
        auto = auto_name_map()
        peers = get_wg_peers()
        detail = []
        for p in sorted(peers, key=lambda x: x["rx"] + x["tx"], reverse=True):
            pr = conn.execute(
                "SELECT rx, tx FROM peer_daily WHERE date=? AND pubkey=?",
                (d, p["pubkey"])).fetchone()
            if pr and (pr[0] or pr[1]):
                detail.append("%s：↓%s ｜ ↑%s" % (
                    peer_display(p, cfg, auto), human(pr[1]), human(pr[0])))
        body = "\n\n".join([
            "**⚠️ 当日流量达到阈值**",
            "今日累计：**%s**（阈值 %g GB）" % (human(rx + tx), cfg.threshold_gb),
            "时间：%s %s（北京时间）" % (d, now_bj(cfg).strftime("%H:%M")),
            "**设备明细**（↓=下载到设备 ｜ ↑=设备上传）  \n" + "  \n".join(detail),
        ])
        ok, msg = notify(cfg, "⚠️ WireGuard 当日流量超阈值", body)
        print("threshold alert: %s (%s)" % ("sent" if ok else "FAILED", msg))
        conn.execute(
            "INSERT INTO alert_state(date,alerted) VALUES(?,1) "
            "ON CONFLICT(date) DO UPDATE SET alerted=1", (d,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 日报

def build_report(cfg):
    d = today_str(cfg)
    conn = open_db()
    try:
        drx, dtx = day_total(conn, d)
        mrx, mtx = month_total(conn, month_prefix(cfg))
        alerted = conn.execute(
            "SELECT alerted FROM alert_state WHERE date=?", (d,)).fetchone()
        auto = auto_name_map()
        rows = conn.execute(
            "SELECT pubkey, rx, tx FROM peer_daily WHERE date=?", (d,)).fetchall()
        peers = {p["pubkey"]: p for p in get_wg_peers()}
        items = []
        for pubkey, rx, tx in rows:
            p = peers.get(pubkey, {"pubkey": pubkey, "ip": ""})
            if rx or tx:
                items.append((rx + tx, peer_display(p, cfg, auto), rx, tx))
        items.sort(reverse=True)
    finally:
        conn.close()

    sep = "-" * 60
    lines = [
        "**WireGuard 流量日报 %s**" % d,
        "",
        "当日(%s): ↓%s ↑%s 合计 %s" % (d, human(dtx), human(drx), human(drx + dtx)),
    ]
    if cfg.threshold_gb > 0:
        pct = min((drx + dtx) * 100.0 / (cfg.threshold_gb * 1024 ** 3), 999)
        lines.append("阈值: %g GB，已用 %.1f%%%s" % (
            cfg.threshold_gb, pct,
            "（今日已告警）" if alerted and alerted[0] else ""))
    lines.append("")
    lines.append(sep)
    lines.append("")
    lines.append("当日各设备用量（↓=下载到设备 ↑=设备上传）:")
    if items:
        for _, name, rx, tx in items:
            lines.append("%s — ↓%s ↑%s" % (name, human(tx), human(rx)))
    else:
        lines.append("暂无流量记录（设备未连接或刚部署）")
    lines.append("")
    lines.append(sep)
    lines.append("")
    lines.append("本月累计: ↓%s ↑%s 合计 %s" % (human(mtx), human(mrx), human(mrx + mtx)))
    title = "WireGuard 流量日报 %s" % d
    # Server酱 按 Markdown 渲染：单个换行会被折叠进同一段落（两台设备挤成一行就是这个原因），
    # 行尾补两个空格才是 Markdown 的强制换行
    return title, "  \n".join(lines)


def daily(cfg):
    title, body = build_report(cfg)
    ok, msg = notify(cfg, title, body)
    print("daily report: %s (%s)" % ("sent" if ok else "FAILED", msg))
    if cfg.auto_update:
        try:
            check_update(cfg, interactive=False)
        except Exception as e:
            print("auto update check failed: %s" % e)
    return ok


# ---------------------------------------------------------------- 推送

def _post(url, data, timeout=15):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(data).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json_loads(resp.read().decode("utf-8"))


def json_loads(s):
    import json
    return json.loads(s)


def send_serverchan(cfg, title, body):
    if not cfg.sendkey:
        return False, "SendKey 未配置"
    url = "https://sctapi.ftqq.com/%s.send" % cfg.sendkey
    data = {"title": title[:32], "desp": body}
    r = _post(url, data)
    if r.get("code") == 0:
        return True, "serverchan ok"
    return False, "serverchan code=%s msg=%s" % (r.get("code"), r.get("message"))


def notify(cfg, title, body):
    """按 provider 推送，失败重试 1 次"""
    senders = {"serverchan": send_serverchan}
    sender = senders.get(cfg.provider)
    if sender is None:
        return False, "未知 provider: %s" % cfg.provider
    last = ""
    for attempt in (1, 2):
        try:
            ok, msg = sender(cfg, title, body)
            if ok:
                return True, msg
            last = msg
            if "code" in msg:  # 业务性错误（如 key 无效）重试也没用
                return False, msg
        except Exception as e:
            last = "exception: %s" % e
    return False, last


# ---------------------------------------------------------------- cron

def install_cron(cfg, verbose=True):
    """安装/刷新定时任务。日报时间按北京时间配置，自动换算为服务器本地时间。"""
    # 北京时间 -> UTC -> 服务器本地（本地 = UTC + 偏移，偏移为整小时故分钟不变）
    utc_hour = (cfg.report_hour - cfg.utc_offset) % 24
    off = datetime.datetime.now().astimezone().utcoffset()
    if off is None or off.total_seconds() % 3600 != 0:
        raise RuntimeError("无法确定服务器整小时时区偏移: %s" % off)
    local_off = int(off.total_seconds() // 3600)
    cron_hour = (utc_hour + local_off) % 24
    cron_minute = cfg.report_minute
    py = sys.executable or "/usr/bin/python3"
    lines = [
        "# wgmon BEGIN",
        "*/%d * * * * %s %s snapshot >> %s 2>&1" % (cfg.snapshot_interval_min, py, os.path.join(BASE_DIR, "wgmon.py"), os.path.join(BASE_DIR, "wgmon.log")),
        "%d %d * * * %s %s daily >> %s 2>&1" % (cron_minute, cron_hour, py, os.path.join(BASE_DIR, "wgmon.py"), os.path.join(BASE_DIR, "wgmon.log")),
        "# wgmon END",
    ]
    try:
        old = subprocess.check_output(["crontab", "-l"], text=True,
                                      stderr=subprocess.DEVNULL)
    except Exception:
        old = ""
    kept = [l for l in old.splitlines()
            if "wgmon" not in l and not l.startswith("# wgmon")]
    new = "\n".join([l for l in kept if l.strip()] + lines) + "\n"
    p = subprocess.run(["crontab", "-"], input=new, text=True)
    if p.returncode != 0:
        raise RuntimeError("crontab 写入失败")
    if verbose:
        print("cron 已安装：快照每 %d 分钟；日报 北京时间 %02d:%02d（服务器本地 %02d:%02d）"
              % (cfg.snapshot_interval_min, cfg.report_hour, cfg.report_minute, cron_hour, cron_minute))


def uninstall(cfg):
    """卸载 wgmon：移除 crontab 定时任务 + 快捷命令；是否删除脚本/数据由用户选择。
    返回 True 表示连文件也删了（调用方应退出）。"""
    print("即将卸载 wgmon：")
    print("  - 移除 crontab 定时任务（快照 + 日报）")
    print("  - 移除快捷命令 /usr/local/bin/wgmon")
    print("  - 不会碰 wg0.conf / wg.sh / WireGuard 本体")
    if input("确定继续? (y/N): ").strip().lower() != "y":
        print("已取消。")
        return False
    # 1) crontab：删除 BEGIN/END 标记之间的行（与 install_cron 对称）
    try:
        old = subprocess.check_output(["crontab", "-l"], text=True,
                                      stderr=subprocess.DEVNULL)
    except Exception:
        old = ""
    kept, skip = [], False
    for l in old.splitlines():
        if l.strip() == "# wgmon BEGIN":
            skip = True
            continue
        if l.strip() == "# wgmon END":
            skip = False
            continue
        if not skip:
            kept.append(l)
    new = "\n".join([l for l in kept if l.strip()])
    p = subprocess.run(["crontab", "-"], input=(new + "\n") if new else "",
                       text=True)
    if p.returncode != 0:
        raise RuntimeError("crontab 移除失败")
    print("定时任务已移除。")
    # 2) 快捷命令软链接
    link = "/usr/local/bin/wgmon"
    if os.path.islink(link):
        try:
            os.remove(link)
            print("快捷命令已移除: %s" % link)
        except OSError as e:
            print("快捷命令移除失败（可手动 rm）: %s" % e)
    # 3) 脚本与数据（可选）
    if input("是否同时删除脚本与流量历史数据（%s，含 wgmon.db）? (y/N): "
             % BASE_DIR).strip().lower() == "y":
        if os.path.isfile(os.path.join(BASE_DIR, "wgmon.py")):
            shutil.rmtree(BASE_DIR)
            print("已删除 %s。" % BASE_DIR)
            return True
    print("已保留 %s（重新启用: python3 %s/wgmon.py install-cron）"
          % (BASE_DIR, BASE_DIR))
    return False


def _backup_and_swap(new_path, target_path):
    """备份旧版后用新版原子替换；新文件继承旧文件权限。返回备份路径（无旧版时 None）。"""
    if os.path.exists(target_path):
        mode = os.stat(target_path).st_mode & 0o777
        bak = "%s.bak-%s" % (
            target_path, datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(target_path, bak)
    else:
        mode = 0o700
        bak = None
    os.chmod(new_path, mode)
    os.replace(new_path, target_path)  # 原子替换，不存在半新半旧状态
    return bak


def cmd_update(cfg):
    """更新器：把新版脚本上传为 .new 文件后执行，校验->备份->原子替换。
    config.ini / wgmon.db / crontab / wg0.conf 全部不受影响。"""
    print("== wgmon 更新器 ==")
    print("新版脚本先上传为以下文件名，再运行本命令（配置/数据/定时任务不受影响）：")
    print("  - wgmon 新版 -> /opt/wgmon/wgmon.py.new")
    print("  - wg.sh 新版 -> /root/wg.sh.new")
    did = False
    # 1) wgmon 自身：py_compile 校验通过才替换
    new_py = os.path.join(BASE_DIR, "wgmon.py.new")
    if os.path.isfile(new_py):
        try:
            py_compile.compile(new_py, doraise=True,
                               cfile=os.path.join(tempfile.gettempdir(), "wgmon_new_check.pyc"))
        except py_compile.PyCompileError as e:
            print("wgmon.py.new 语法校验失败，已跳过（未做任何改动）：%s" % e)
        else:
            bak = _backup_and_swap(new_py, os.path.join(BASE_DIR, "wgmon.py"))
            print("wgmon 已更新%s。新代码自下次运行生效（当前菜单还是旧代码）。"
                  % ("（旧版备份: %s）" % bak if bak else ""))
            did = True
    # 2) wg.sh：bash -n 语法校验通过才替换；wg.sh 是交互脚本，替换不影响运行中的隧道
    new_sh = "/root/wg.sh.new"
    if os.path.isfile(new_sh):
        p = subprocess.run(["bash", "-n", new_sh], capture_output=True, text=True)
        if p.returncode != 0:
            print("wg.sh.new 语法校验失败，已跳过（未做任何改动）：%s"
                  % (p.stderr or "")[:300])
        else:
            bak = _backup_and_swap(new_sh, "/root/wg.sh")
            print("wg.sh 已更新%s。wg.sh 为交互脚本，替换不影响运行中的隧道。"
                  % ("（旧版备份: %s）" % bak if bak else ""))
            did = True
    if not did:
        print("未发现 .new 文件，没有任何改动。")
    print("确认新版正常后，可删除备份：rm -f %s/wgmon.py.bak-* /root/wg.sh.bak-*"
          % BASE_DIR)


# ---------------------------------------------------------------- 自动更新

REPO_PREFIX = "wg-traffic-monitor"  # 仓库内 wgmon 目录名


def _ver_tuple(v):
    """'1.10.2' -> (1,10,2)，用于新旧版本比较"""
    parts = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in parts) if parts else (0,)


def fetch_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "wgmon/%s" % VERSION})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_first(cfg, rel_path):
    """依次尝试 GitHub raw 与 jsdelivr 镜像，返回内容（阿里云北京两个通道均实测可达）"""
    errs = []
    for base in (cfg.repo_base, cfg.mirror_base):
        if not base:
            continue
        try:
            return fetch_text(base.rstrip("/") + "/" + rel_path.lstrip("/"))
        except Exception as e:
            errs.append("%s -> %s" % (base, e))
    raise RuntimeError("; ".join(errs) if errs else "未配置更新源")


def check_update(cfg, interactive=False):
    """检查仓库版本号，有新版则下载->校验->备份->原子替换（配置/数据/cron 不动）。
    返回 0=无更新 1=已更新 2=失败"""
    try:
        remote = fetch_first(cfg, REPO_PREFIX + "/VERSION").strip()
    except Exception as e:
        if interactive:
            print("检查更新失败（网络或仓库不可达）: %s" % e)
        else:
            print("update check failed: %s" % e)
        return 2
    if _ver_tuple(remote) <= _ver_tuple(VERSION):
        if interactive:
            print("已是最新版本 v%s（远端 v%s）" % (VERSION, remote))
        else:
            print("update check: up to date (v%s)" % VERSION)
        return 0
    print("发现新版本：v%s → v%s" % (VERSION, remote))
    if interactive:
        try:
            if input("立即更新? (Y/n): ").strip().lower() == "n":
                print("已取消。")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            return 0
    try:
        code = fetch_first(cfg, REPO_PREFIX + "/wgmon.py")
    except Exception as e:
        print("下载失败: %s" % e)
        return 2
    new_py = os.path.join(BASE_DIR, "wgmon.py.new")
    with open(new_py, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    try:
        py_compile.compile(new_py, doraise=True,
                           cfile=os.path.join(tempfile.gettempdir(), "wgmon_new_check.pyc"))
    except py_compile.PyCompileError as e:
        os.remove(new_py)
        print("新版语法校验失败，已放弃更新（当前版本未改动）: %s" % e)
        return 2
    bak = _backup_and_swap(new_py, os.path.join(BASE_DIR, "wgmon.py"))
    print("已更新到 v%s%s。新代码自下次运行生效。" % (remote, "（备份: %s）" % bak if bak else ""))
    notify(cfg, "wgmon 已自动更新 v%s → v%s" % (VERSION, remote),
           "新版 v%s 已自动安装完成，配置与流量数据不受影响。\n\n旧版备份：%s\n时间：%s（北京时间）"
           % (remote, bak or "无", now_bj(cfg).strftime("%Y-%m-%d %H:%M")))
    return 1


# ---------------------------------------------------------------- 子命令

def cmd_selftest(cfg):
    open_db().close()
    print("数据库初始化 OK: %s" % DB_PATH)
    peers = get_wg_peers()
    print("wg 采集 OK: %d 个 peer" % len(peers))
    auto = auto_name_map()
    for p in peers:
        print("  - %s -> %s (rx=%s tx=%s)" % (
            p["ip"], peer_display(p, cfg, auto), human(p["rx"]), human(p["tx"])))
    ok, msg = notify(cfg, "✅ wgmon 部署成功",
                     "WireGuard 流量监控已部署。\n\n这是一条测试消息，收到即表示微信推送链路正常。\n\n- 快照：每 10 分钟\n- 日报：北京时间 %02d:%02d\n- 当日阈值：%g GB"
                     % (cfg.report_hour, cfg.report_minute, cfg.threshold_gb))
    print("推送测试: %s%s" % ("成功" if ok else "失败", "" if ok else " -> " + msg))
    return 0 if ok else 1


def cmd_status(cfg):
    d = today_str(cfg)
    conn = open_db()
    try:
        drx, dtx = day_total(conn, d)
        mrx, mtx = month_total(conn, month_prefix(cfg))
        row = conn.execute("SELECT alerted FROM alert_state WHERE date=?", (d,)).fetchone()
        auto = auto_name_map()
        rows = conn.execute(
            "SELECT pubkey, rx, tx FROM peer_daily WHERE date=?", (d,)).fetchall()
        peers = {p["pubkey"]: p for p in get_wg_peers()}
        items = []
        for pubkey, rx, tx in rows:
            p = peers.get(pubkey, {"pubkey": pubkey, "ip": ""})
            if rx or tx:
                items.append((rx + tx, peer_display(p, cfg, auto), rx, tx))
        items.sort(reverse=True)
    finally:
        conn.close()
    total = drx + dtx
    print("== wgmon 状态 (%s 北京时间) ==" % now_bj(cfg).strftime("%Y-%m-%d %H:%M"))
    print("当日(%s): ↓%s ↑%s 合计 %s" % (d, human(dtx), human(drx), human(total)))
    if cfg.threshold_gb > 0:
        pct = min(total * 100.0 / (cfg.threshold_gb * 1024 ** 3), 999)
        print("阈值: %g GB，已用 %.1f%%%s" % (
            cfg.threshold_gb, pct, "（今日已告警）" if row and row[0] else ""))
    print("-" * 60)
    if items:
        print("当日各设备用量（↓=下载到设备 ↑=设备上传）:")
        for _, name, rx, tx in items:
            print("%s — ↓%s ↑%s" % (name, human(tx), human(rx)))
    else:
        print("当日各设备用量: （暂无数据，等待下次快照）")
    print("-" * 60)
    print("本月累计: ↓%s ↑%s 合计 %s" % (human(mtx), human(mrx), human(mrx + mtx)))


def cmd_report(cfg):
    title, body = build_report(cfg)
    print(title)
    print("-" * 40)
    print(body)


# ---------------------------------------------------------------- 交互菜单

def press_enter_to_menu():
    """wg.sh 风格：操作完成后暂停，回车才返回菜单（防止结果被刷屏顶掉）"""
    try:
        input("\n按回车返回菜单...")
    except (EOFError, KeyboardInterrupt):
        pass


def cmd_menu(cfg):
    while True:
        print()
        print("======== wgmon 流量监控 ========")
        print("1) 查看当日/本月用量")
        print("2) 立即推送日报到微信")
        print("3) 发送测试消息")
        print("4) 修改当日告警阈值（当前 %g GB，0=禁用）" % cfg.threshold_gb)
        print("5) 修改日报推送时间（当前北京时间 %02d:%02d）" % (cfg.report_hour, cfg.report_minute))
        print("6) 修改快照间隔（当前 %d 分钟）" % cfg.snapshot_interval_min)
        print("7) 修改 Server酱 SendKey")
        print("8) 重装定时任务")
        print("9) 卸载 wgmon（移除定时任务/快捷命令，可选删数据）")
        print("10) 更新 wgmon / wg.sh（上传 .new 文件后执行，保留配置）")
        print("11) 检查并安装更新（GitHub，当前 v%s）" % VERSION)
        print("12) 自动更新（每天日报时自动检查安装，当前 %s）" % ("开" if cfg.auto_update else "关"))
        print("0) 退出")
        choice = input("请选择: ").strip()
        if choice == "0":
            break
        if choice == "1":
            cmd_status(cfg)
        elif choice == "2":
            daily(cfg)
        elif choice == "3":
            ok, msg = notify(cfg, "✅ wgmon 测试消息", "这是一条手动测试消息。")
            print("发送: %s%s" % ("成功" if ok else "失败", "" if ok else " -> " + msg))
        elif choice == "4":
            try:
                v = float(input("新阈值 (GB，0=禁用): ").strip())
                if v < 0:
                    raise ValueError
                cfg.threshold_gb = v
                save_config(cfg)
                print("已保存。")
            except ValueError:
                print("输入无效。")
        elif choice == "5":
            try:
                h = int(input("小时 (0-23): ").strip())
                m = int(input("分钟 (0-59): ").strip())
                if not (0 <= h <= 23 and 0 <= m <= 59):
                    raise ValueError
                cfg.report_hour, cfg.report_minute = h, m
                save_config(cfg)
                install_cron(cfg)
            except ValueError:
                print("输入无效。")
        elif choice == "6":
            try:
                v = int(input("新快照间隔（分钟，建议 5/10/15/30/60）: ").strip())
                if v < 1 or v > 1440:
                    raise ValueError
                cfg.snapshot_interval_min = v
                save_config(cfg)
                install_cron(cfg)
            except ValueError:
                print("输入无效。")
        elif choice == "7":
            k = input("新 SendKey (SCT 开头): ").strip()
            if k.startswith("SCT") and len(k) > 10:
                cfg.sendkey = k
                save_config(cfg)
                print("已保存。可用菜单 3 验证。")
            else:
                print("SendKey 格式不像，未保存。")
        elif choice == "8":
            try:
                install_cron(cfg)
            except Exception as e:
                print("失败: %s" % e)
        elif choice == "9":
            if uninstall(cfg):
                print("wgmon 已完全卸载。")
                break
        elif choice == "10":
            cmd_update(cfg)
        elif choice == "11":
            check_update(cfg, interactive=True)
        elif choice == "12":
            cfg.auto_update = not cfg.auto_update
            save_config(cfg)
            print("自动更新已%s（%s）。" % (
                "开启" if cfg.auto_update else "关闭",
                "每天日报时自动检查 GitHub 并安装新版" if cfg.auto_update else "不再自动检查"))
        else:
            print("无效选项，请重新选择。")
            continue
        press_enter_to_menu()


def main():
    cfg = load_config()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "menu"
    try:
        if cmd == "snapshot":
            snapshot(cfg, verbose=True)
        elif cmd == "daily":
            sys.exit(0 if daily(cfg) else 1)
        elif cmd == "selftest":
            sys.exit(cmd_selftest(cfg))
        elif cmd == "status":
            cmd_status(cfg)
        elif cmd == "report":
            cmd_report(cfg)
        elif cmd == "install-cron":
            install_cron(cfg)
        elif cmd == "uninstall":
            uninstall(cfg)
        elif cmd == "update":
            cmd_update(cfg)
        elif cmd == "check-update":
            sys.exit(check_update(cfg, interactive=True))
        elif cmd == "version":
            print("wgmon v%s" % VERSION)
        elif cmd == "menu":
            cmd_menu(cfg)
        else:
            print("用法: wgmon.py [snapshot|daily|selftest|status|report|install-cron|uninstall|update|check-update|version|menu]")
            sys.exit(2)
    except RuntimeError as e:
        print("ERROR: %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
