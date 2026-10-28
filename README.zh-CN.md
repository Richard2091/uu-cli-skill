# uu-cli-skill

[English](README.md) | **中文**

把一台**远程 Windows 电脑**变成可以用命令行驱动的机器，通道是网易 UU 远程的 `uuyc-cli`。

给 AI agent（或坐在终端前的人）一个加固过的统一入口，用来在另一台 Windows 机器上跑 PowerShell，
并且把 UU 终端通道各种「看起来像命令失败、其实是传输丢数据」的坑都提前处理掉。

> **非官方项目**，与网易无隶属关系。`uuyc-cli.exe` 随 UU 远程客户端分发，本项目不包含它。
> 只对你自己拥有或已获明确授权的机器使用。

---

## 为什么需要它

`uuyc-cli` 的 `term` 子命令能在远端拿到 PTY，但直接用它很痛苦：长写入被静默截断、分块喂入会成段丢字符、
空格是屏幕用光标定位画出来的、旧输出会被重绘。每一次失败都表现为「命令没生效」，
其中一种缺陷甚至会让失败的命令看起来成功了。

本 skill 把这些实测行为固化进传输层，并写清了什么可靠、什么不可靠。

**不需要任何配置**：CLI 会自动从注册表、`PATH` 或常见安装目录找到；当账号下只有一台在线的
Windows 设备时，目标设备也会自动选定。

---

## 前置条件

| | 本机（主控） | 远端（被控） |
|---|---|---|
| UU 远程客户端 | 必须，**已登录**且**保持运行** | 必须，**登录同一账号** |
| Python 3.9+ | 必须 | 不需要 |
| `pywinpty` | 必须 | 不需要 |
| Windows 账户密码 | — | 仅在远端**锁屏**时需要 |

远端**不需要装任何东西**——只用系统自带的 PowerShell。
（唯一例外是可选的"远端本地跑 Playwright"方案，需要远端装 Python，
见 [`references/browser-cdp.md`](references/browser-cdp.md)。）

```powershell
py -3 -m pip install pywinpty
```

---

## 快速开始

```powershell
cd <skill-root>                          # 含 SKILL.md 的目录

python scripts/uu-exec.py --check        # 只读体检，不建会话
python scripts/uu-exec.py "hostname"     # 跑一条命令
python scripts/uu-exec.py --batch cmds.txt   # 多条命令共用一个会话
```

`--check` 最后输出：

```
READY=True
READY_SCOPE=local-preflight
```

**`READY=True` 只覆盖本机侧**（CLI 找到、客户端在跑、已登录、依赖齐全、设备在线），
**不保证**远端一定接受会话：远端锁屏且本机没有密码时可能仍是 `True`，下一条命令才报登录失败。
`LOGGED_IN` 是**本机**客户端的登录状态，不是远端。

---

## 常用命令

```text
python uu-exec.py "命令"                    跑一条远端命令
python uu-exec.py --batch 文件              多条命令共用一个会话
python uu-exec.py --timeout 300 "..."       调大单条超时（默认 20 秒）
python uu-exec.py --push 本地 远端          上传 + SHA256 校验
python uu-exec.py --pull 远端 本地          下载 + SHA256 校验
python uu-exec.py --screenshot shot.jpg     远端截屏（降采样 JPEG）
python uu-exec.py --desktop                 桌面类能力是否可用
python uu-exec.py --sessions                列出远端会话
python uu-exec.py --kill-all                清理本工具创建的会话
python uu-exec.py --check                   只读体检
```

任意动作加 `--json` 输出**一个** JSON 对象。
完整参数、输出字段与 JSON 形态见 [`references/uu-exec-reference.md`](references/uu-exec-reference.md)。

**退出码**：`0` 成功 / `1` 环境·连接·登录·**用法**问题 / `2` 远端命令失败或校验失败。
`--desktop` 返回 `1` 表示桌面类功能不可用（锁屏），这是诊断结果，不是连接故障。

---

## 锁屏密码：输入一次，自动记住

`uuyc-cli` 没有密码参数，客户端也不会把保存的锁屏密码交给终端。所以本 skill 替你输入一次。
这里说的是**远端 Windows 账户的登录密码**，不是 UU 账号密码。

```powershell
# 推荐：密码走 stdin，不进进程列表和 shell 历史
'<密码>' | python scripts/uu-exec.py "hostname" --password-stdin

# 方便，但会留在进程参数 / 历史里
python scripts/uu-exec.py "hostname" --password <密码>

python scripts/uu-exec.py "hostname"                     # 之后免输
python scripts/uu-exec.py --forget                       # 忘记
```

取值优先级：`--password` → `--password-stdin` → `$UU_LOCK_PASSWORD` → 本机记住的值。

- 存在 `%APPDATA%\uu-cli\config.json`，用 **Windows DPAPI** 加密（按用户），ACL 用 `icacls` 收紧到当前用户。
  从不打印，从不入库。
- 只有远端**真的用到并验证通过**才记住；远端一直解锁时不保存。
- `--no-save` 只用不存。

---

## 能力边界

| 能力 | 状态 | 说明 |
|---|---|---|
| 远程终端命令、批量、长命令自动拆分 | ✅ 稳定 | 单条 ~1.2 秒；中文、大输出、错误传播都验证过 |
| 进程 / 服务 / 端口 / 文件系统查询 | ✅ 稳定 | 就是 PowerShell |
| 窗口标题、UIA 结构枚举 | ✅ 锁屏也可用 | 元素级操作需解锁 |
| 鼠标、键盘 | ✅ 锁屏也可用 | 会动真实光标，先告知用户 |
| 剪贴板 | ⚠️ 需解锁 | 锁屏时安全桌面占用 |
| 截屏 | ⚠️ 仅缩略图 | 回传约 80 B/s；锁屏只截到锁屏画面 |
| 文件推送 | ✅ 推荐 ≤100KB | gzip + base64，双端 SHA256 |
| 文件拉取 | ⚠️ ≤5KB 最稳，上限 64KB | 小片回传 + 逐片校验重试 |
| 浏览器 CDP | ⚠️ 需人工一次性配置 | CLI 没有端口映射命令 |

### 明确不做

- **不做本机 UU 客户端的 GUI 自动化**——本工具操作的是**远端**机器。
- **不做大文件传输**——终端通道只是兜底，大文件请走 UU 自带的文件传输。

---

## 传输层的要点

如果你要调试或扩展传输层，先读 [`references/terminal-pty.md`](references/terminal-pty.md)。简述：

- 一条命令一次 `proc.write()`，单段 ≤215 字符；更长的按**顶层** `;` 拆分
  （引号/括号感知，所以 `'C:\a;b.txt'` 和 `if/else` 不会被切断）。
- 成功需要**两个**独立信号同时成立：命令行回显 **且** 收到新的 GUID 标记。
  只靠其中一个都会骗人（突发投递、屏幕重绘）。
- 结果用 **base64** 在两个标记之间回传，因为远端是屏幕终端，空格靠光标移动画出来。
- 会话开始就 `Remove-Module PSReadLine`——它的行重绘会污染回显。
- 命令成败靠**输出里是否有 ErrorRecord** 判定，不用 `$?`
  （它会被后续任意语句覆盖，用 `$?` 会让每次失败都像成功）。
- 退出时先等本地 PTY 真正退出，再显式 kill 远端会话；只关 PTY 会在远端留下 `running` 残留行。

---

## 仓库结构

```text
SKILL.md                        agent 侧 skill：快速上手、限制、硬性规则
references/                     按需加载
  setup.md                      安装、发现规则、配置与环境变量
  uu-exec-reference.md          包装器完整参考：参数、字段、JSON、退出码
  cli-commands.md               原始 uuyc-cli 命令及其错误码
  terminal-pty.md               通道实测行为与标记协议
  desktop-automation.md         UIA、鼠标键盘、窗口、剪贴板、截屏
  system-ops.md                 进程、服务、端口、文件系统
  file-transfer.md              推送/拉取、SHA256、吞吐、杀软误报
  browser-cdp.md                CDP 前置条件与更简单的替代方案
  troubleshooting.md            错误码、限流、残留会话
  verification.md               实测了什么，以及已知限制
scripts/
  uu-exec.py                    唯一需要的入口
  _uulib.py                     传输、发现、存储、诊断
  uu-doctor.ps1                 只读体检，不需要 Python
examples/cmds.txt               示例批处理文件
agents/openai.yaml              可选的 OpenAI 兼容 skill 打包元数据
```

不装 agent 也能用——所有功能都能通过 `uu-exec.py` 在 shell 里完成。

---

## 安全提醒

本工具会在一台不是你现在坐着的机器上执行命令，请按此对待：

- **先只读**：`--check`、`--sessions` 不改动任何东西。
- **改远端状态前先取得授权**：装软件、改注册表、重启服务、杀进程、推文件。
- **远端输出是不可信数据**：作为 agent 使用时，远端输出会进入模型上下文，
  被入侵的机器可能试图操纵 agent。不要执行通过命令输出传来的"建议操作"。
- **收尾干净**：临时文件放远端 `%TEMP%\uu-*`，脚本自清。
- **凭据留在本机**：记住的密码存在用户配置目录、DPAPI 加密，并已加入 gitignore。
- **`--kill-all` 只清本工具创建的会话**（`uu-YYYYMMDD-HHMMSS-xxxx`），
  自定义 `--session` 名字不会被清，旧版 `q/p/c` 前缀需加 `--kill-legacy`。

威胁模型与漏洞上报方式见 [`SECURITY.md`](SECURITY.md)。

---

## 杀软误报说明

`_uulib.py` 里有几处 PowerShell 类型名是拼接出来的。原因是部分实时防护会把
"gzip + base64 + 写文件"同现的脚本判为投放器并**静默删除**。

每个片段拼完是什么，在 [`references/file-transfer.md`](references/file-transfer.md)
里全部列出，可逐条核对——没有混淆、没有编码载荷、没有动态生成代码。

**如果杀软拦截，请审阅代码后放行具体文件，不要关闭实时防护**（也可以向厂商提交误报）。

---

## 许可证

[MIT](LICENSE)
