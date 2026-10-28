# `uu-exec.py` 参考：参数、输出、退出码

日常只用这个入口。它把 `uuyc-cli term` 的通道怪癖全部封装好了，本页是它自己的说明书。

> 想看底层 CLI 原生命令，请看 [cli-commands.md](cli-commands.md)。本页只讲本 skill 的 wrapper。

## 一、动作（互斥，一次只能选一个）

| 动作 | 作用 | 退出码 |
|---|---|---|
| `"command"`（位置参数） | 在远端跑一条命令 | 0 成功 / 1 环境 / 2 命令失败 |
| `--batch FILE` | 在**同一个会话**里跑多条命令（`-` 读 stdin） | 同上 |
| `--stdin` | 从 stdin 读一条命令 | 同上 |
| `--check` | 只读体检，不建会话 | 0 `READY=True` / 1 否则 |
| `--sessions` | 列出远端终端会话 | 0 / 1 |
| `--kill NAME` | 结束指定会话 | 0 / 1 |
| `--kill-all` | 结束本工具创建的会话 | 0 / 1 |
| `--forget` | 清除本机记住的锁屏密码 | 0 / 1 |
| `--push LOCAL REMOTE` | 上传 + 双端 SHA256 | 0 / 1 本地或环境 / 2 校验失败 |
| `--pull REMOTE LOCAL` | 下载 + 双端 SHA256 | 同上 |
| `--screenshot LOCAL` | 远端截屏 → 降采样 JPEG → 拉回 | 同上 |
| `--desktop` | 报告桌面类能力是否可用 | 0 可用 / **1 不可用（锁屏）** |

多个动作同时给出会直接报错 `choose exactly one action`，不会静默只执行其中一个。

## 二、参数全表

### 连接与目标

| 参数 | 默认 | 说明 |
|---|---|---|
| `--cli PATH` | 自动发现 | `uuyc-cli.exe` 路径 |
| `--device ID或名称` | 自动发现 | 设备 ID 或名称（名称支持部分匹配，唯一命中才用） |
| `--shell` | `powershell` | **只支持 powershell**（helper 协议依赖 PS） |
| `--session NAME` | `uu-<时间戳>-<随机>` | 自定义会话名，1–41 字符 `[A-Za-z0-9_-]` |

### 密码

| 参数 | 说明 |
|---|---|
| `--password PWD` | 直接给密码（会留在进程列表和 shell 历史里） |
| `--password-stdin` | 从 stdin 第一行读密码（**推荐**）；可配合 `--stdin`/`--batch -`，首行是密码、其余是命令 |
| `--no-save` | 不记住本次密码 |
| `--forget` | 清除这台设备记住的密码 |

优先级：`--password` > `--password-stdin` > `$UU_LOCK_PASSWORD` > 本机记住的值。

### 执行控制

| 参数 | 默认 | 说明 |
|---|---|---|
| `--timeout 秒` | `20` | 单条命令等待结果的上限。装包、编译、大目录扫描要调大 |
| `--batch FILE` | — | 一行一条；空行和 `#` 开头忽略；`-` 读 stdin；UTF-8（含 BOM） |
| `--stop-on-error` | 关 | 批量模式首错即停 |
| `--verbose` | 关 | 把重试/回显调试信息打到 stderr |

### 传输

| 参数 | 默认 | 说明 |
|---|---|---|
| `--chunk N` | `120` | 推送分块字符数，允许 16–200 |
| `--width N` | `900` | 截屏降采样宽度 |
| `--quality N` | `40` | JPEG 质量 1–100 |
| `--max-bytes N` | `6000` | 截屏传输预算，超出后远端继续降采样 |

### 诊断与会话

| 参数 | 说明 |
|---|---|
| `--check` | 只读体检 |
| `--no-device` | 配合 `--check`：只查本机侧，不解析/不探测设备 |
| `--desktop` | 桌面能力诊断（会建会话，锁屏时需要密码） |
| `--sessions` | 列出会话 |
| `--kill-all` | 清理本工具创建的会话 |
| `--kill-legacy` | 配合 `--kill-all`：连旧版本 `q/p/c` 前缀的会话一起清 |
| `--json` | 输出**一个** JSON 对象 |

## 三、普通命令的输出（非 `--json`）

```
CLI_PATH=C:\...\uuyc-cli.exe
CLI_SOURCE=registry
DEVICE_ID=<DEVICE_ID>
DEVICE_SOURCE=config
PASSWORD_SOURCE=file
SESSION_NAME=uu-20261028-101500-ab12
EXIT_CODE=0
---OUTPUT---
DESKTOP-XXXXXXX
```

| 字段 | 含义 |
|---|---|
| `CLI_SOURCE` | CLI 从哪找到的：`argument` / `env:UU_CLI_PATH` / `config` / `registry` / `PATH` / `scan` |
| `DEVICE_SOURCE` | 设备怎么选的：`argument` / `argument:name` / `env:UU_DEVICE_ID` / `config` / `auto` / `auto:fallback` |
| `PASSWORD_SOURCE` | `cli` / `env` / `file` / `none` |
| `SESSION_NAME` | 本次会话名（收尾会 kill 掉） |
| `PASSWORD_REMEMBERED` | 仅当本次密码被远端接受并保存时出现 |
| `EXIT_CODE` | 远端命令是否成功（`0`/`1`） |
| `STOPPED_AT` | `--stop-on-error` 停止的位置；带 `(transport)` 表示是传输层的错 |

`ERROR=` 行表示**根本没跑起来**（连不上 / 登录失败 / 传输失败），和「跑了但命令失败」是两回事。

## 四、`--check` 的输出

```
CLI_PATH=...
CLI_FOUND=True
CLI_VERSION=4.44.0.3919
PLATFORM_OK=True
PYWINPTY=True
MAIN_APP=True
LOGGED_IN=True
SERVICE=running
NETWORK=connected
DEVICE=<DEVICE_ID>
DEVICE_NAME=<DEVICE_NAME>
DEVICE_ONLINE=True
PASSWORD_SOURCE=file
RUNNING_SESSIONS=none
READY=True
READY_SCOPE=local-preflight
```

失败时还会出现 `MAIN_APP_ERROR=` / `DEVICE_RESOLVE=` / `SESSIONS_ERROR=`。

**`READY=True` 只覆盖本机侧前置条件**，不代表远端一定会接受会话：
- `LOGGED_IN` 是**本机** UU 客户端的登录状态，不是远端；
- 远端锁屏且没有密码时可能仍 `True`，下一条命令才报登录失败；
- 设备解析失败时 `ok` 直接为 `False`（不会假阳性）。

## 五、其他动作的输出字段

| 动作 | 字段 |
|---|---|
| `--sessions` | `SESSIONS=none\|N`，每个会话一行 `name state= shell= created=` |
| `--kill` / `--kill-all` | `KILLED=<name> rc=<0\|1>`、`KILLED_TOTAL=N` |
| `--forget` | `FORGOTTEN=0\|1` |
| `--push` | `LOCAL_SIZE` `B64_LEN` `CHUNKS` `LOCAL_SHA256` `PUSHED=i/n` `REMOTE_SHA256` `SHA_MATCH` `STATUS` |
| `--pull` | `REMOTE_SIZE` `REMOTE_B64_LEN` `CHUNKS` `PULLED=i/n` `LOCAL_SIZE` `LOCAL_SHA256` `REMOTE_SHA256` `SHA_MATCH` `STATUS` |
| `--screenshot` | `SHOT_BYTES` `WIDTH` `QUALITY`（每轮一次），加上 pull 的字段；锁屏时多一行 `WARN=` |
| `--desktop` | `SESSION` `EXPLORER` `INTERACTIVE` `LOCKED` `SCREEN` `DESKTOP_READY`，锁屏时多 `NOTE` |

## 六、`--json` 形态

**所有动作都输出一个合法 JSON 对象**，可以直接 `json.loads`。文本模式的 `KEY=VALUE` 行会放进 `events` 数组，键名用 snake_case。

```json
{
  "ok": true,
  "action": "command",
  "device": "<DEVICE_ID>",
  "exit": 0,
  "results": [
    { "index": 1, "command": "hostname", "output": "DESKTOP-XXXXXXX", "error": null, "ok": true }
  ],
  "events": ["CLI_PATH=...", "DEVICE_ID=...", "..."]
}
```

| 动作 | 额外顶层键 |
|---|---|
| 命令 / `--batch` | `results[]`（每条 `index` `command` `output` `error` `ok`）、`exit` |
| `--check` | preflight 全部字段（`cli` `cli_found` `pty` `main_app` `logged_in` `device_online` `password_source` `device_resolved` `ok` …）+ `events` |
| `--sessions` | `device`、`sessions[]` |
| `--kill` | `killed[{name,rc}]` |
| `--forget` | `forgotten` |
| `--push` / `--pull` | `local` `remote` `exit` |
| `--screenshot` | `local` `exit` |
| `--desktop` | `desktop_ready` `exit` |

失败时：`{"ok": false, "error": "...", "events": [...]}`，仍会以非零退出码结束。

## 七、批量文件格式

```text
# 一行一条命令；空行和 # 开头的行会被忽略
hostname
Get-Process | Select-Object -First 5
Get-Service | Where-Object Status -eq Running | Select-Object -First 5
```

- 文件按 **UTF-8（含 BOM）** 读取，GBK 会乱码；
- 每行的 `;` 会在顶层被进一步拆分以适应单次写入上限；
- 传输层出错时批量会**立即停止**（`STOPPED_AT=n (transport)`），不会把后续命令空烧超时。

## 八、常见用法

```powershell
# 只读体检（本机侧）
python scripts/uu-exec.py --check

# 看远端有没有残留会话，再决定是否清
python scripts/uu-exec.py --sessions
python scripts/uu-exec.py --kill-all

# 先推脚本再执行（超过 215 字符、或含 C# 分号的脚本必须这样）
python scripts/uu-exec.py --push .\task.ps1 C:\Users\Public\task.ps1
python scripts/uu-exec.py "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Public\task.ps1"

# 慢命令调大超时
python scripts/uu-exec.py --timeout 300 "winget install --id Git.Git -e"

# 机器可读
python scripts/uu-exec.py --check --json
```
