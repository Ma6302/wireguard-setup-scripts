# wireguard-setup-scripts

WireGuard 组网服务器一键安装 / 管理脚本（阿里云 Ubuntu 实测），附 SSH 加固与出方向防护脚本。

## 作者

| 角色 | 作者 |
|---|---|
| 原作者（二改汉化） | 包崽同学 |
| 优化维护 | **Ma6302**（[github.com/Ma6302](https://github.com/Ma6302)） |

上游血统：脚本基于 [Nyr 的 openvpn-install](https://github.com/Nyr/openvpn-install) 系 WireGuard 分支（版权行见脚本运行横幅），由包崽同学二改汉化，Ma6302 在其上做了 11 项修复与体验优化（F1~F11，详见 [CHANGELOG.md](CHANGELOG.md)）。

## 一键部署（新服务器）

在目标服务器上以 root 执行（**先下载再运行**，不要用 `curl ... | bash`——管道会吃掉 wg.sh 与 SendKey 的交互输入）：

```bash
curl -fsSL -o /root/deploy.sh https://raw.githubusercontent.com/Ma6302/wireguard-setup-scripts/main/deploy.sh \
  && bash /root/deploy.sh
```

国内网络访问 GitHub 慢时，可换 CDN 镜像（同样内容）：

```bash
curl -fsSL -o /root/deploy.sh https://cdn.jsdelivr.net/gh/Ma6302/wireguard-setup-scripts@main/deploy.sh \
  && bash /root/deploy.sh
```

引导脚本会依次完成：下载 `wg.sh` 与 wgmon（GitHub raw 主通道 + jsdelivr 兜底，均带语法校验）
→ 放置 `/root/wg.sh`（已有则先备份再替换）
→ 未装 WireGuard 时自动拉起 `wg.sh` 交互安装
→ 安装 wgmon 到 `/opt/wgmon`（保留已有 `config.ini`）、装定时任务、建 `wgmon` 快捷命令。

| 参数 | 作用 |
|---|---|
| `--dry-run` | 只下载并校验，不修改服务器任何文件 |
| `--sendkey=KEY` | 非交互指定 Server酱 SendKey（不填则安装时提示输入） |
| `--no-wg` | 跳过 wg.sh（WireGuard 已装好时用） |
| `--ref=REF` | 指定 git ref（分支/标签）。默认自动解析为**最新的 `vX.Y.Z` Release 标签**（整仓不可变快照）；加 `--ref=main` 可跟随分支最新提交 |

> 部署完成后**不需要**再手动 `wgmon check-update`——当天日报后会自动检查；想立刻升级就执行一次。

## 内容

| 文件 | 说明 |
|---|---|
| `wg.sh` | 主脚本：WireGuard 服务端一键安装 + 管理菜单（添加客户端 / **管理已有客户端**（列出·删除·QR 码）/ **网络优化开关** / 卸载，`wg show` 显示 peer 名称） |
| `WGSH_VERSION` | **wg.sh 版本声明**（仓库根）。`wgmon` 的自更新用它和服务器上 `/root/wg.sh` 内的 `WG_SH_VERSION` 标记比对 |
| `harden-ssh.sh` | SSH 加固（root 仅密钥登录、关闭密码登录；幂等、可回滚，`--check` 只读查看） |
| `wg-forward-guard.sh` + `.service` | WireGuard 出方向防护：抑制客户端异常扫描/P2P 行为，防止云平台误判「对外攻击」触发全端口阻断 |
| `wg-traffic-monitor/` | **流量监控 + 微信日报**（wgmon）：增量记账统计各设备/当日/当月流量，阈值告警，定时日报，支持 **wgmon 与 wg.sh 双组件** GitHub 自动更新 |
| `deploy.sh` | **一键部署引导**（新服务器）：下载 wg.sh + wgmon → 校验 → 放置 → 按需拉起 WireGuard 安装 → 装监控与定时任务。支持 `--dry-run` / `--sendkey=` / `--no-wg` / `--ref=` |
| `publish-via-api.py` | **维护者发布工具**：本机 git 通道不通时，用 GitHub Git Data API 逐层重建提交（哈希与本地一致、历史不分叉）。见下方「发布」 |
| `CHANGELOG.md` | 完整迭代日志（A→C9 共 12 个版本，每项修复的原理、部署与验证记录） |
| `docs/versions/` | 全部历史版本快照（文件名内嵌行数与 md5 前 8 位） |

## 使用

```bash
# 安装 / 管理（root）
bash wg.sh

# SSH 加固（先 --check 看现状，确认后再执行）
bash harden-ssh.sh --check
bash harden-ssh.sh

# 出方向防护
bash wg-forward-guard.sh on|off|status

# 流量监控 + 微信日报（详见 wg-traffic-monitor/README.md）
cd wg-traffic-monitor && bash install.sh
```

## 流量监控（wgmon）

`wg-traffic-monitor/` 是零成本流量监控：不动隧道本体，只读 `wg show all dump` 做增量记账。

- 每晚 23:35 微信日报：当日总量 / 阈值使用率 / 各设备用量 / 本月累计
- 当日累计达到阈值（默认 20 GB）立即告警，每天最多 1 次
- 交互菜单**二级化**（v1.4.1 起，与 wg.sh 同级风格）：主菜单 9 项，「参数设置」（阈值/日报时间/快照间隔）
  与「更新管理」（手动更新/检查更新/自动更新开关）各自收进二级菜单，可返回上一级
- **自动更新（双组件，从 Release 标签快照）**：每天日报后检查最新项目标签，两个组件**各自**比对版本 ——
  - `wgmon`：标签内 `wg-traffic-monitor/VERSION` vs 本地 `VERSION` 常量
  - `wg.sh`：标签根 `WGSH_VERSION` vs 服务器 `/root/wg.sh` 内的 `WG_SH_VERSION` 标记（老脚本无标记则更新一次对齐）

  **谁有新版本更新谁**，校验（版本一致 + 语法：`py_compile` / `bash -n`）→ 备份旧版 → 原子替换，
  微信通知写明「更新了哪个」。`wg.sh` **只换文件，不执行、不影响运行中的隧道**（raw + jsdelivr 双通道锁定同一标签）。
  菜单 `7) 更新管理` 里可开关自动更新、手动检查；`update_wgsh = false` 可单独关掉 wg.sh 的自动更新
- **单版本流发布（维护者）**：tag 指向整仓快照（wg.sh + wgmon + deploy.sh 都在里面），
  **无需为未改动的组件重复上传文件**；组件是否升级由各自版本文件决定：
  ```bash
  git tag v1.4.0 && git push origin v1.4.0
  gh release create v1.4.0 --title "v1.4.0" --notes "本次变更：..."
  ```
  只推 main 不打标签的提交**不会**被已部署服务器安装。
  完整流程（含发版前检查、发版后回读验证、**git 推不上去时的 API 绕行**）见
  [`wg-traffic-monitor/README.md`](wg-traffic-monitor/README.md)；绕行脚本：`publish-via-api.py`

⚠️ 部署生成的 `config.ini` 含 SendKey，已被 `.gitignore` 排除，请勿手动提交。

要求：Ubuntu 22.04（Debian 系亦可），root 权限。适配发行版列表见 `wg.sh` 内 `case` 分支（Ubuntu / Debian / AlmaLinux / Rocky / CentOS / Fedora / openSUSE）。

## 脚本亮点

- **F1~F6**：修复原版 DNS 配置被清空、非法兜底 DNS、DNS 菜单死分支、IPv6 误判等问题
- **F7**：防火墙放行规则跟随自定义端口（原版写死 53 端口）
- **F8**：客户端 `AllowedIPs` 的 `::/0` 门控——服务器无 IPv6 时不再下发，避免 IPv6 黑洞
- **F9**：`wg show` 每个 peer 显示名称（终端场景附加，管道/脚本调用输出与原版逐字节一致）
- **F10**：主菜单循环化——一次运行可连续执行多个操作；EOF/取消均安全返回
- **F11**：菜单二级化——主菜单 6 项精简为 4 项，三个客户端管理操作收进「管理已有客户端」二级菜单
  （进入即显示已有客户端清单）；同时脚本内新增 `WG_SH_VERSION` 版本标记，供 wgmon 自动更新比对
- **F12**：**网络优化开关**（v1.4.2 起，主菜单 3，**默认开启**）——fq_codel 队列 + `netdev_max_backlog=10000`
  内核入口缓冲，实测回程抖动 mdev 改善约 40%（突发/拥塞时偶发延迟尖峰明显减少）；切换即时生效、
  不影响在线隧道；关闭的选择持久化（重装/升级沿用），卸载后重装回到默认开启
  （v1.4.3 起：从旧版升级上来的服务器首次运行新版会**自动补开**，用户明确关闭过的则不动）

## 免责声明

- 本脚本仅在阿里云轻量应用服务器 Ubuntu 22.04 上实测，其他环境请自行评估。
- 请遵守所在地区法律法规与云服务商服务条款。在阿里云上，将服务器用作公网流量穿透出口属于违规行为，请仅用于合法的私有组网。
- `docs/versions/` 中的历史快照仅供溯源，其中早期版本（A/B/S）含已知致命 bug，**切勿部署**。

## License

[MIT](LICENSE) © 2026 Ma6302。包含的上游代码遵循其原始许可证（版权声明保留于脚本内）。
