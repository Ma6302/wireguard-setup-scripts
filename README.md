# wireguard-setup-scripts

WireGuard 组网服务器一键安装 / 管理脚本（阿里云 Ubuntu 实测），附 SSH 加固与出方向防护脚本。

## 作者

| 角色 | 作者 |
|---|---|
| 原作者（二改汉化） | 包崽同学 |
| 优化维护 | **Ma6302**（[github.com/Ma6302](https://github.com/Ma6302)） |

上游血统：脚本基于 [Nyr 的 openvpn-install](https://github.com/Nyr/openvpn-install) 系 WireGuard 分支（版权行见脚本运行横幅），由包崽同学二改汉化，Ma6302 在其上做了 10 项修复与体验优化（F1~F10，详见 [CHANGELOG.md](CHANGELOG.md)）。

## 内容

| 文件 | 说明 |
|---|---|
| `wg.sh` | 主脚本：WireGuard 服务端一键安装 + 管理菜单（添加/删除客户端、QR 码、peer 名称显示） |
| `harden-ssh.sh` | SSH 加固（root 仅密钥登录、关闭密码登录；幂等、可回滚，`--check` 只读查看） |
| `wg-forward-guard.sh` + `.service` | WireGuard 出方向防护：抑制客户端异常扫描/P2P 行为，防止云平台误判「对外攻击」触发全端口阻断 |
| `CHANGELOG.md` | 完整迭代日志（A→C6 共 9 个版本，每项修复的原理、部署与验证记录） |
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
```

要求：Ubuntu 22.04（Debian 系亦可），root 权限。适配发行版列表见 `wg.sh` 内 `case` 分支（Ubuntu / Debian / AlmaLinux / Rocky / CentOS / Fedora / openSUSE）。

## 脚本亮点

- **F1~F6**：修复原版 DNS 配置被清空、非法兜底 DNS、DNS 菜单死分支、IPv6 误判等问题
- **F7**：防火墙放行规则跟随自定义端口（原版写死 53 端口）
- **F8**：客户端 `AllowedIPs` 的 `::/0` 门控——服务器无 IPv6 时不再下发，避免 IPv6 黑洞
- **F9**：`wg show` 每个 peer 显示名称（终端场景附加，管道/脚本调用输出与原版逐字节一致）
- **F10**：主菜单循环化——一次运行可连续执行多个操作；EOF/取消均安全返回

## 免责声明

- 本脚本仅在阿里云轻量应用服务器 Ubuntu 22.04 上实测，其他环境请自行评估。
- 请遵守所在地区法律法规与云服务商服务条款。在阿里云上，将服务器用作公网流量穿透出口属于违规行为，请仅用于合法的私有组网。
- `docs/versions/` 中的历史快照仅供溯源，其中早期版本（A/B/S）含已知致命 bug，**切勿部署**。

## License

[MIT](LICENSE) © 2026 Ma6302。包含的上游代码遵循其原始许可证（版权声明保留于脚本内）。
