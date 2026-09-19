# wgmon —— WireGuard 流量监控 + 微信日报

零成本方案：Python3 标准库（无 pip 依赖）+ cron + Server酱免费额度（5 条/天，实际用量 ≤2 条/天）。
**纯只读监控**：只执行 `wg show all dump`，不碰 wg0.conf / wg.sh / 任何系统服务。

## 功能
- **每晚 23:30（北京时间）**推送日报到微信：当日总量（↓下载/↑上传）、阈值使用率、各设备用量、本月累计
- **当日累计 ≥ 阈值（默认 20 GB）** 立即告警（每天最多 1 次，跨天自动重置；阈值可在菜单改，0=禁用）
- 快照间隔可配置（默认 **30 分钟**）：增量记账（wg/服务器重启清零有兜底逻辑，不会出现负数）
- **自动更新**：每天日报时比对仓库版本号（`VERSION`），有新版自动下载→校验→备份→原子替换
- 交互菜单（wg.sh 同款交互，操作后回车返回菜单）

## 原理
WireGuard 内核为每个 peer 维护**自接口启动起累计**的 rx/tx 字节计数；脚本每 N 分钟读一次并求增量，
按「日期 + 设备」累加进 SQLite：

```
当日某设备用量 = 该设备今天所有增量之和
当日总量      = 当天全部设备求和
本月累计      = 当月各天求和（date LIKE 'YYYY-MM-%'）
```

- 设备名：由 peer 内网 IP 匹配 `/root/<设备名>.conf` 的 `Address` 自动得出，可用 `[peers]` 手动覆盖
- 兜底：新读数 < 旧读数（wg/服务器重启）→ 该次增量取新读数，保证不为负
- 缺口：重启瞬间到下一次快照之间的流量无法追回（≤1 个快照间隔，可忽略）

## 文件
| 文件 | 用途 |
|---|---|
| `wgmon.py` | 全部逻辑（单文件，仅 Python3 标准库） |
| `VERSION` | 版本号（自动更新用，与脚本内 `VERSION` 常量比较） |
| `config.example.ini` | 配置模板；部署后为 `/opt/wgmon/config.ini`（⚠️ 含 SendKey，勿提交到仓库） |
| `install.sh` | 一键部署（不覆盖已有配置） |

## 部署
```bash
# 1. 上传本目录到服务器（如 /root/wg-traffic-monitor）
# 2. 在服务器上：
cd /root/wg-traffic-monitor && bash install.sh
# 3. 填入 SendKey（sct.ftqq.com 微信扫码获取），再自检：
python3 /opt/wgmon/wgmon.py selftest
# 4. 可选：装快捷命令
ln -sf /opt/wgmon/wgmon.py /usr/local/bin/wgmon
```
依赖：已安装 WireGuard（有 `wg` 命令）、root 权限、python3（Ubuntu/Debian 自带）。

## 常用操作
```bash
wgmon menu         # 交互菜单：查用量/立即推送/改阈值/改时间/改间隔/改SendKey/检查更新/开关自动更新/卸载
wgmon status       # 命令行看当日/本月用量与各设备明细
wgmon report       # 只打印日报文本（不推送）
wgmon version      # 查看当前版本
wgmon check-update # 立即检查并安装新版
wgmon update       # 用上传的 wgmon.py.new / wg.sh.new 本地更新（保留配置）
wgmon selftest     # 自检 + 发测试消息
```

菜单项：`1` 查看用量 · `2` 立即推送日报 · `3` 测试消息 · `4` 阈值 · `5` 日报时间 ·
`6` 快照间隔 · `7` SendKey · `8` 重装定时任务 · `9` 卸载 · `10` 手动更新（本地 .new）·
`11` 检查并安装更新（GitHub）· `12` 自动更新开关 · `0` 退出

## 更新机制
- **自动**：`auto_update = true` 时，每天日报推送后检查 `VERSION`，发现新版 →
  下载 → `py_compile` 校验 → 旧版备份 `wgmon.py.bak-时间戳` → 原子替换 → 微信通知。
  校验失败或网络不通则**完全不动**当前版本（只写日志）
- **手动**：菜单 `11)` 或 `wgmon check-update`
- **离线**：把新版上传为 `/opt/wgmon/wgmon.py.new`，执行 `wgmon update`（支持 wg.sh 的 `/root/wg.sh.new`）
- 更新只替换脚本文件；`config.ini` / `wgmon.db` / crontab / wg0.conf 全部不受影响
- 更新源：GitHub raw 为主，jsdelivr CDN 自动兜底

## 卸载
菜单 `9)`，或：
```bash
wgmon uninstall                    # 交互确认：移除定时任务+快捷命令，可选删数据
```
对隧道零影响（本来就没改过任何隧道相关的东西）。

## 已知边界
- 服务器重启期间的流量缺口不可恢复（≤1 个快照间隔）
- Server酱免费版每天 5 条：日报 1 条，告警/更新通知按需各 1 条，余量充足
- 推送失败只记日志不重试（业务错误如 SendKey 失效，重试无意义）
- 设备名自动映射适配 `/root/<设备名>.conf` 布局；wg-easy / PiVPN 等在 `[peers]` 手动填
