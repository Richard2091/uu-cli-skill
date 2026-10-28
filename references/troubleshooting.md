# 排错指南

先跑 `python scripts/uu-exec.py --check`。它一次报出 CLI、主程序、登录、设备、残留会话的真实状态。
**不要在没看 `--check` 输出的情况下重试。**

## 目录

- [一、第一次用就失败（最常见）](#一第一次用就失败最常见)
- [二、登录与密码](#二登录与密码)
- [三、会话与限流](#三会话与限流)
- [四、命令执行问题](#四命令执行问题)
- [五、桌面与传输](#五桌面与传输)
- [六、错误码对照](#六错误码对照)
- [七、安全软件干扰](#七安全软件干扰)
- [八、每次开工的固定顺序](#八每次开工的固定顺序)

## 一、第一次用就失败（最常见）

| 现象 | 原因 | 处理 |
|---|---|---|
| `can't open file 'scripts/uu-exec.py'` | 不在 skill 目录下 | `cd <skill-root>` 或用绝对路径 |
| `ERROR=uuyc-cli not found` | CLI 没找到 | 装 UU 远程，或设 `UU_CLI_PATH` / `--cli` |
| `PYWINPTY=False` | 没装 pywinpty | `py -3 -m pip install pywinpty` |
| 装完仍 `PYWINPTY=False` | Python 与 pip 不是同一个解释器 | 用 `py -3 -m pip install`，再用 `py -3 scripts\uu-exec.py` |
| `MAIN_APP=False` | UU 客户端没运行 | 启动并登录客户端（除 `--version` 外都需要它） |
| `LOGGED_IN=False` | 本机客户端未登录 | 登录 UU 账号 |
| `DEVICE_ONLINE=False` | 远端没开机/没联网/未登录 | 打开被控机，确认两端登录同一账号 |
| `no online Windows device` | 账号下没有在线 Windows 设备 | 打开远端，或 `--device <ID>` |
| `several online Windows devices` | 多台在线 | `--device <名称或ID>` 明确指定（不会替你猜） |
| `ERROR=uu-cli runs on Windows only` | 在 Linux/macOS 上跑 | 本工具只支持 Windows |

`--check` 里 `DEVICE_RESOLVE=` 有内容且 `READY=False` 时，先解决设备解析，别急着跑命令。

## 二、登录与密码

### `ERROR=login failed (the remembered password was rejected)`

密码错了，或**远端改过密码**。重新带一次：

```powershell
'<新密码>' | python scripts/uu-exec.py "hostname" --password-stdin
```

若确定密码正确但仍失败，先怀疑**限流**（见下节），不要反复重试把限流窗口拉长。

### `the remote is locked and no password is available`

远端锁屏且本机没有密码。带一次密码即可，之后自动记住。

### 远端明明没锁屏，却提示要密码

- 远端可能刚锁屏；用 `--desktop` 看 `LOCKED`。
- 或者上次留下的错误密码被记住了：`--forget` 后重来。

### 密码会不会被泄露

- 存在 `%APPDATA%\uu-cli\config.json`，用 Windows DPAPI 加密，ACL 收紧到当前用户。
- `--password` 会进进程列表和 shell 历史；**优先用 `--password-stdin` 或 `$UU_LOCK_PASSWORD`**。
- 只有远端**真的用到**并验证通过才会保存。

## 三、会话与限流

### `the service is rate-limiting term connections` / `操作过于频繁`

服务端限制短时间内的 `term` 连接数。

- 等 10–30 秒再试；
- 减少 churn：用 `--batch` 把多条命令塞进一个会话，不要每条命令开一次；
- `--kill-all` 内部按 1.5 秒间隔逐个 kill，避免自己触发限流。

### `Terminal session is already attached (code 2001)`

同一个会话只能被一个客户端持有。原持有者（另一个终端、或上一个未退出的进程）还连着。

- 关掉那个客户端；或
- 直接用本 skill 的入口——它每次自建新会话，不会去抢别人的。

### `Error: terminal session not found`

`term ls` 里那行 `running` 是**陈旧的**：持有者已消失，服务端没清理记录。

```powershell
python scripts/uu-exec.py --kill-all
```

不影响新建会话，但会累积垃圾行，定期清一次。

### 退出码 127：设备正在被远控

两种可能：

1. 别人正在远控 → 等待或协调；
2. 你自己上次的 PTY 崩溃，服务端锁还没释放 → 等 3–5 分钟，再 `--sessions` / `--kill-all`。

**预防**：不要直接 kill 本地 PTY 进程；用脚本的收尾逻辑（默认已做）。

### `--kill-all` 会杀掉别人的会话吗

不会。它只匹配本工具生成的名字：`uu-YYYYMMDD-HHMMSS-xxxx` 或旧版 `uu<5位数字>`。
`core` / `prod` / `python` 这类名字不会被误伤。
旧版本遗留的 `q/p/c` 前缀需要显式加 `--kill-legacy`。
用 `--session` 自定义的名字**不会**被 `--kill-all` 清理，要用 `--kill <名字>`。

## 四、命令执行问题

### `no marker within 20s` / `no echo within 20s`

单条命令没拿到完整往返。脚本已自动重试 4 次。

- 偶发：正常，通道是突发投递的，重试即过。
- **慢命令**：装包、编译、大目录扫描很容易超过 20 秒 → `--timeout 300`。这是最常见的原因。
- **每个会话开头就出现、且每条命令都失败**：多半是辅助函数定义没写全。
  脚本会自动重写定义并重试；自己写脚本时要"每条定义单独写 + 分别确认"。
- 持续失败：查杀软是否在扫描脚本块、链路是否正常。

### 命令失败了却显示 `EXIT_CODE=0`

这是旧版本的缺陷，已修复。现在用输出里的 `ErrorRecord` 判定失败，标记形如 `@guid@0|1@base64#`，
第二个字段就是成败。若仍遇到，确认用的是最新 `_uulib.py`。

### `command has a segment that still exceeds one write after splitting on ";"`

单段命令超过 215 字符，且没有可拆的顶层 `;`。

- 用 `--batch` 拆成多条；
- 或推脚本：`--push task.ps1` 然后 `-File` 执行；
- 注意：`Add-Type -TypeDefinition` 里的 C# 分号**不是**命令分隔符，这类脚本必须推文件。

### `decode failed: 'utf-8' codec can't decode byte ...`

长单行输出在回传时被打断（通道已知缺陷）。

- 缩小输出：`| Select-Object -First 5`、只取需要的属性；
- 或让远端落文件再 `--pull`（分片重试）；
- 不要试图一次拉大段文本。

### 命令跑很久没反应

先确认是不是命令本身慢（默认 20 秒超时太低）。再确认远端是否弹了交互式提示
（比如 `winget` 的确认、`Read-Host`）——这类交互在非交互终端里会挂住。
用带静默参数的版本，或把交互输入写进脚本。

## 五、桌面与传输

### 剪贴板读写失败 / 截屏只有锁屏画面

远端处于锁屏，安全桌面占用了这些资源。

```powershell
python scripts/uu-exec.py --desktop     # 看 LOCKED / DESKTOP_READY
```

`DESKTOP_READY=False` 时：窗口标题、UIA 枚举、进程服务仍可用；
**不要承诺**剪贴板、截屏内容、UIA 元素级操作。解锁远端后再试。

注意 `--desktop` 自己也要建会话，锁屏无密码时会在登录阶段失败。

### `--pull` 报文件太大

上限 64KB，但推荐 ≤5KB。通道约 80 B/s。改用 UU 图形界面传，然后 `Get-FileHash` 核对。

### `SHA256 mismatch`

传输被破坏。重试一次；若持续，缩小文件或改用图形界面。本地不会留下损坏文件。

## 六、错误码对照

### `uu-exec.py` 的退出码（wrapper）

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 1 | 环境 / 连接 / 登录 / **用法**问题 |
| 2 | 远端命令失败，或传输校验失败 |

`--desktop` 返回 1 表示"桌面类功能不可用（锁屏）"，是**诊断结果**，不是连接故障。

### `uuyc-cli` 的原始退出码

`101` 未登录、`102` 连不上主程序、`105` 超时/需密码/限流/版本不兼容、`106` 设备或会话不存在、
`108` 需 PTY、`110` 设备离线、`127` 被远控。

**这些不会直接成为 wrapper 的退出码**——wrapper 统一收敛成 0/1/2，
原始码出现在 `ERROR=` 的文本里。完整表见 [cli-commands.md](cli-commands.md)。

## 七、安全软件干扰

### 脚本被静默删除

**现象**：`scripts/*.py` 几秒后消失，运行报 `No such file`。

**原因**：部分实时防护把"gzip + base64 + 写文件"的组合判为投放器特征
（本机在火绒上真实发生过）。`_uulib.py` 因此把几个 PowerShell 类型名拆开书写，
完整展开见 [file-transfer.md](file-transfer.md)——**没有混淆，也没有隐藏行为**。

**处理**：审阅代码后对**你信任的具体文件**放行，或向厂商提交误报。**不要关闭实时防护。**

### 命令莫名变慢 / 超时

安全软件扫描脚本块，或输入投递突发延迟。重试 1–2 次即可。
判定成功靠"回显 + 新标记"，不靠时间——不要因为一次慢就改协议。

## 八、每次开工的固定顺序

1. `python scripts/uu-exec.py --check` — 只读，看 `READY`
2. `python scripts/uu-exec.py --sessions` — 有残留先 `--kill-all`
3. 干活：能用一条命令就别推脚本；多步用 `--batch`；慢命令调 `--timeout`
4. 收尾：`--sessions` 确认 `SESSIONS=none`，远端临时文件清干净
