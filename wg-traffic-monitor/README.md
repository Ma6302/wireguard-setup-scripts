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
| `install.sh` | 一键部署（本目录内的单组件安装，不覆盖已有配置） |

> 新服务器整体部署请用**仓库根目录**的 `deploy.sh`（同时装 wg.sh + wgmon，见根目录 README）；
> 发布工具 `publish-via-api.py` 也在仓库根目录。

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
- **更新来源（v1.3.0 起）**：默认 `channel = release`——从**最新的项目 Release 标签**
  （`vX.Y.Z`，整仓不可变快照；兼容旧的 `wgmon-vX.Y.Z`）取文件，标签是不可变快照，
  比 main 分支的中间状态安全；找不到标签时自动回退 main 分支。
  下载走 raw 主通道 + jsdelivr 兜底，**两路锁定同一个标签**。
- **是否需要更新以组件自己的版本为准**：先读「标签内 `wg-traffic-monitor/VERSION`」与本地比较，
  版本没变就不下载。因此**只改了 wg.sh 的版本不会让服务器白装一次 wgmon**。
- **完整性校验**：`VERSION` 比对通过后，再校验下载文件内的 `VERSION` 常量与之一致，最后
  `py_compile` 校验语法；任何一步不过就放弃并保留当前版本（不留下 `.new` 残留）
- **自动**：`auto_update = true` 时，每天日报推送后检查一次；发现新版 → 校验 → 旧版备份
  `wgmon.py.bak-时间戳` → 原子替换 → 微信通知（含来源标签）
- **手动**：菜单 `11)` 或 `wgmon check-update`；开关：菜单 `12)`；查看通道：`wgmon version`
- 更新只替换脚本文件；`config.ini` / `wgmon.db` / crontab / wg0.conf 全部不受影响

## 卸载
菜单 `9)`，或：
```bash
wgmon uninstall                    # 交互确认：移除定时任务+快捷命令，可选删数据
```
对隧道零影响（本来就没改过任何隧道相关的东西）。

## 发布新版本（维护者）
**一个版本流、一个标签就够了**：tag 指向整仓快照，`wg.sh` / `wgmon.py` / `deploy.sh` 都在同一棵树里，
因此**不需要**为了"另一个组件没改"而重复上传文件或附件；组件是否需要更新由各自的 `VERSION` 决定。

1. 改代码。若改的是 wgmon，**同步提升**脚本内 `VERSION` 常量与 `wg-traffic-monitor/VERSION`（两处必须一致）；
   只改 wg.sh 则**不动** wgmon 的版本号（这样服务器检查后会发现组件版本没变，不会白装）
2. `git add -A && git commit && git push`（**推不上去时见下方「上传通道不通怎么办」**）
3. 打标签并发布 Release（推荐 `vX.Y.Z`；发布说明里注明本次改了哪个组件）：
   ```bash
   git tag v1.3.1 && git push origin v1.3.1
   gh release create v1.3.1 --title "v1.3.1" --notes "本次变更：wgmon 1.3.1（...）"
   gh release upload v1.3.1 wg-traffic-monitor/wgmon.py --clobber   # 附件可选，仅为人工下载方便
   ```
4. 已部署服务器会在**当天日报后自动升级**（仅当它对应组件有新版本），或立刻 `wgmon check-update`
5. 只推 main 不打标签 → 走 `channel = release` 的服务器**不会**升级（这正是"只发布已验证版本"的保险）

### 上传通道不通怎么办（本机代理只放行 `api.github.com`）

> **先直接试 `git push`** —— 实测该通道是**间歇性**的（同一天里时通时不通），通了就不必绕行。

若 `git push` 报 `CONNECT tunnel failed, response 502`、`Failed to connect to github.com port 443`
或长时间挂住，说明 git-over-HTTPS 通道此刻不可用。**两条绕行路线**：

**路线 A（推荐，最省事）**：开着 Clash Verge 再推 —— git **不读** Windows 系统代理，必须显式指定：
```bash
git -c http.proxy=http://127.0.0.1:7897 push
# 7897 是 Clash Verge 的 mixed 端口；用前先确认 Clash 已启动、端口在听
```

**路线 B（不需要代理）**：用 GitHub **Git Data API** 逐层重建提交 —— 脚本 `publish-via-api.py`
（项目主目录 / 仓库根目录都有）：
```bash
python publish-via-api.py --dry-run     # 先看会做什么（含防误造分叉的前置检查）
python publish-via-api.py               # 实际推送
```
它走 `api.github.com`（通常可达），顺序是 **blob → tree → commit → PATCH ref**，
作者 / 提交时间 / 提交信息全部照抄本地，因此**远端 commit 哈希与本地完全一致**，历史不分叉、
不需要事后 `fetch && reset`。任一级哈希对不上就终止、不推进 ref（宁可不推也不造分叉）。

> **注意**：`gh release create` 在 Windows 上不认 `/tmp` 路径（gh 是原生 Windows 程序），
> `--notes-file` 请用仓库内**相对路径**；附件用**位置参数**或 `gh release upload`，
> gh 2.99 的 `--attach` 会报 usage 错误。大附件给足 timeout（100 MB 量级 ≥10 分钟）。
> 打完包**务必回读验证**（见下方「发版检查清单」），"命令没报错"不等于发布成功。

## 发版检查清单（维护者）
发布前后各跑一遍。**"命令没报错"不等于成功，必须有回读证据**——本项目实测过
`gh release create --attach` 静默报 usage 错误、`gh repo create` 参数错却仍返回 0 的情况。

### 发版前
1. **本地与服务器脚本一致**：`md5sum wg-traffic-monitor/wgmon.py` 与服务器 `/opt/wgmon/wgmon.py` 相同
2. **工作区干净**：`git status --short` 无输出；`git log --oneline -1` 就是本次发版内容
3. **两处版本号同时提升且一致**（仅当改了 wgmon）：`wgmon.py` 内 `VERSION` 常量 + `wg-traffic-monitor/VERSION`
4. **只改 wg.sh 时不动 wgmon 版本号**——否则已部署服务器会白装一次
5. **敏感信息扫描**：`grep -rn "SCT" wg-traffic-monitor/`（`config.ini` 含 SendKey，绝不能入库）

### 发版后回读验证（不可跳过）
```bash
TAG=v1.4.0
REPO=Ma6302/wireguard-setup-scripts

# 1) commit SHA：本地 == 远端
[ "$(git rev-parse HEAD)" = "$(gh api repos/$REPO/commits/main --jq .sha)" ] && echo SHA_OK

# 2) tag 指向的就是这个 commit
gh api repos/$REPO/git/ref/tags/$TAG --jq .object.sha

# 3) 标签内文件与本地逐字节一致（自动更新取的就是这里）
curl -fsSL "https://raw.githubusercontent.com/$REPO/$TAG/wg-traffic-monitor/wgmon.py" | md5sum
md5sum wg-traffic-monitor/wgmon.py     # 两个 md5 必须相同

# 4) 传了附件时，state 必须全部是 uploaded（不是 "starter"/"uploading"）
gh release view $TAG --repo $REPO --json assets \
  --jq '.assets[] | "\(.name) \(.size) \(.state)"'
```

> **gh 在 Windows 上的两个坑**：`--notes-file` 要用仓库内相对路径（不认 `/tmp`）；
> 附件用**位置参数**（`gh release create <tag> <file>`）或 `gh release upload`，
> gh 2.99 的 `--attach` 会报 usage 错误。详见上方「上传通道不通怎么办」。

### 服务器侧验收
```bash
wgmon check-update     # 期望："已是最新版本 vX.Y.Z（来源: Release 标签 vX.Y.Z）"
```
或等当天日报（北京时间 23:35）后自动检查——结果只写 `/opt/wgmon/wgmon.log`，不成功也不影响监控。

## 已知边界
- 服务器重启期间的流量缺口不可恢复（≤1 个快照间隔）
- Server酱免费版每天 5 条：日报 1 条，告警/更新通知按需各 1 条，余量充足
- 推送失败只记日志不重试（业务错误如 SendKey 失效，重试无意义）
- 设备名自动映射适配 `/root/<设备名>.conf` 布局；wg-easy / PiVPN 等在 `[peers]` 手动填
