# `uuyc-cli` 原生命令参考

> 日常请用 `uu-exec.py`（见 [uu-exec-reference.md](uu-exec-reference.md)）。
> 本页只在**排错或扩展**时需要：它讲的是底层 CLI 本身。

基于 `uuyc-cli` v4.43–4.44 实测。查询类命令支持 `--json`，输出统一信封：

```json
{
  "success": true,
  "data": { },
  "error": null,
  "timestamp": "2026-01-01T00:00:00Z"
}
```

失败时 `success=false`，`error.code` / `error.message` 填充，`data=null`。

> **解析注意**：CLI 把进度信息写到 **stderr**，JSON 写到 **stdout**。
> 不要拼接两者再解析（会得到 `Extra data`）。先解析 stdout，失败再试 stderr。
> **`success=false` 必须当错误处理**，不能当成"空结果"。

## CLI 发现

```powershell
$instDir = (Get-ItemProperty 'HKLM:\SOFTWARE\Netease\GameViewerSetup' -EA 0).InstDir
$cli = Join-Path $instDir 'bin\uuyc-cli.exe'
```

默认不在 PATH。除 `--version` 外都需要主程序运行。

## 基础命令

| 命令 | 说明 | 关键字段 |
|---|---|---|
| `--version` | CLI 版本（无需主程序） | 纯文本 |
| `echo [msg] --json` | 通信测试 | `data.message`, `data.connection` |
| `status --json` | 诊断 | `data.isLoggedIn`, `data.serviceStatus`, `data.networkStatus`, `data.appVersion`, `data.osVersion` |

`status` 的 `isLoggedIn` 是**本机**客户端登录状态。

## 用户

| 命令 | 说明 |
|---|---|
| `user info --json` | `data.userId`, `data.username`, `data.nickname`, `data.isVip` |
| `user wallet --json` | 钱包余额 |

## 设备管理

| 命令 | 说明 | 异步 |
|---|---|---|
| `device list --json` | 列出设备 | – |
| `device info ID --json` | 设备详情 | – |
| `device connect ID --json` | 发起远控（建立画面） | ✅ |
| `device disconnect [ID] --json` | 断开（无 ID = 全部） | – |
| `device status --json` | 连接状态 | – |

`device list` → `data.items[]`，每项含 `deviceId`、`deviceName`、`platform`、`isOnline`。

`device status` → `data.connections[]`，看 `connectionState`：`connecting` / `connected`（`isControlling: true`）/ `failed`（看 `lastError`）。

## 云电脑

| 命令 | 说明 | 异步 |
|---|---|---|
| `cloudpc list --json` | 列出云电脑 | – |
| `cloudpc info [ID] --json` | 详情 | – |
| `cloudpc status [ID] --json` | 连接状态 | – |
| `cloudpc launch ID --json` | 开机 | ✅ |
| `cloudpc shutdown ID --json` | 关机（**必须带 ID**） | ✅ |
| `cloudpc connect ID --json` | 连接（须已开机） | ✅ |
| `cloudpc disconnect [ID] --json` | 断开（无 ID = 全部） | – |

## 远程协助

| 命令 | 说明 |
|---|---|
| `assist id --json` | `data.assistId` |
| `assist reset-code --json` | 刷新验证码 |
| `assist set-code [CODE] --json` | 设置固定码（或 `--code-stdin`） |
| `assist allow on/off --json` | `data.enabled`, `data.changed` |
| `assist start ID [CODE] --json` | 发起协助 ✅异步 |
| `assist status [ID] --json` | 协助进展 |

## 远程终端（term）

需要 `-d DEVICE_ID`。`term new` / `attach` 接管交互式终端，不支持 `--json`；其余子命令支持。

| 命令 | 说明 | 需要 PTY | 锁屏需密码 |
|---|---|:---:|:---:|
| `term -d ID new [NAME] [--shell powershell]` | 新建 + 接管 | ✅ | ✅ |
| `term -d ID attach [NAME]` | 接入已有会话 | ✅ | ✅ |
| `term -d ID ls --json` | 列出会话 | – | ✅ |
| `term -d ID has NAME --json` | 判断存在（退出码 0/1） | – | ✅ |
| `term -d ID rename OLD NEW --json` | 重命名 | – | ✅ |
| `term -d ID kill NAME --json` | 结束会话 | – | ✅ |

`term ls` → `data.items[]`，含 `name`、`state`（running/exited）、`shell`、`createdAt`。
注意 `state` **不可信**：持有者消失后仍可能显示 `running`（陈旧行）。

`term new` 的**选项只有 `--shell`**，没有密码参数；密码只能经 stdin 输入。
底层 CLI 支持 `--shell cmd`，但本 skill 只支持 PowerShell——helper 协议依赖 PowerShell。

**锁屏时这些短命令也要密码，但可经 stdin 传入**（不需要开 PTY）：

```powershell
# 注意：这样密码会进入 shell 历史，仅作说明用；实际请用 --password-stdin
'<密码>' | & $cli term -d $dev ls --json
```

## 本地终端（lterm）

同 `term` 但无 `-d` 参数，操作**本机**。`--shell` 仅 `powershell|cmd`。本 skill 不使用。

## 异步命令

`device connect` / `cloudpc launch` / `cloudpc shutdown` / `cloudpc connect` / `assist start`
都是异步提交，必须轮询对应的 `status` 确认最终状态，不能假定成功。

## 危险操作

- `device disconnect` 不带 ID = **断开全部**连接。
- `cloudpc shutdown` 必须指定 ID。
- 只 kill 自己创建的终端会话。

## 一、CLI 原始退出码

这些是 `uuyc-cli` / `term` 自己返回的码，**不等于** `uu-exec.py` 的退出码
（wrapper 只返回 0/1/2，见 [uu-exec-reference.md](uu-exec-reference.md)）。

| 码 | 含义 |
|---|---|
| 0 | 成功（`has`：存在） |
| 1 | `has`：不存在（非错误） |
| 2 | 用法错误 / 主程序未运行 |
| 101 | 未登录 |
| 102 | 无法连接主程序或终端服务 |
| 104 | 参数无效 / 会话名已占用 |
| 105 | 超时 / 响应不可用 / **需密码** / **限流** / 版本不兼容 |
| 106 | 设备或会话不存在 |
| 108 | 当前环境无法接管终端（非交互式调用 `term new`） |
| 110 | 设备离线 |
| 118/119 | 协助验证超时/失败 |
| 127 | 设备正在被远控（服务端锁） |

## 二、`term` 消息码

`term` 的一部分错误不在退出码里，而是出现在输出文本中：

| 消息码 / 文本 | 含义 | 处理 |
|---|---|---|
| `2001` / `Terminal session is already attached` | 该会话已被另一个客户端持有 | 关掉那个客户端，或让本工具自建新会话 |
| `terminal session not found` | 会话不存在（`ls` 里的 `running` 是陈旧行） | `--kill-all` 清掉 |
| `操作过于频繁` / rate limit | 服务限流 | 等 10–30 秒；减少会话 churn |
| `正在被远控` / `currently being remotely controlled` | 设备被他人远控 | 等待或协调 |
| `版本过低` | 协议不兼容 | 升级**主控端**客户端 |
| `检测到被控端已锁屏，请输入被控端账户密码` | 锁屏需密码 | 用 `--password-stdin` 提供一次 |

## 三、`127` 的两种触发条件

`127` 既可能是「别人正在远控」，也可能是「你自己上一次的 PTY 崩溃，服务端锁还没释放」。
两者处理不同：

- 别人在用 → 等待或协调，不要暴力重试；
- 自己的残留 → 等 3–5 分钟后 `--sessions` 确认，再 `--kill-all`。
