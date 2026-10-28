# 安装与配置

本 skill 不绑定任何一台机器。换主控机、换被控机、换 UU 版本，按本页配置一次即可。

> 非官方项目，与网易无关。`uuyc-cli.exe` 随 UU 远程客户端分发，本 skill 不包含它。

## 一、主控端（你执行命令的机器）

| 依赖 | 用途 | 安装 |
|---|---|---|
| UU 远程客户端（含 `uuyc-cli.exe`） | 与远端通信的唯一通道 | 从官网安装 UU 远程 / GameViewer |
| Python 3.9+ | 运行 `uu-exec.py` | `winget install Python.Python.3`（或用 `py` 启动器） |
| `pywinpty` | `term` 需要真实 TTY，纯管道会返回退出码 108 | `py -3 -m pip install pywinpty` |

```powershell
py -3 --version
py -3 -m pip install pywinpty
py -3 scripts\uu-exec.py --check
```

**注意**：确保 `python` / `py` 与安装 `pywinpty` 用的是**同一个解释器**，否则会报 `pywinpty is required`。

可选：`scripts/uu-doctor.ps1` 是纯 PowerShell 体检，完全不需要 Python。

`--check` 是**只读**的：不建会话、不改远端。全部满足时最后是 `READY=True` + `READY_SCOPE=local-preflight`。

## 二、被控端（远端）

| 项 | 要求 |
|---|---|
| 系统 | Windows 10 / 11（实测 PowerShell 5.1） |
| UU 远程客户端 | 已安装并**登录同一账号**，否则设备列表里看不到它 |
| 电源与网络 | 已开机且在线（`DEVICE_ONLINE=True`） |
| Windows 账户密码 | 远端**锁屏**时需要（一次即可记住）；不是 UU 账号密码 |
| 交互桌面 | 剪贴板、截屏内容、UIA 元素级操作需要**已解锁** |
| Python / agent | **不需要**。只在远端用系统自带 PowerShell |

唯一例外：如果你想在远端跑 Playwright，那是另一个可选方案，需要远端自行装 Python，见 [browser-cdp.md](browser-cdp.md)。

## 三、两台机器都要装吗

| | 主控（本机） | 被控（远端） |
|---|:---:|:---:|
| UU 远程客户端 | 必须，且**保持运行** | 必须 |
| 登录同一 UU 账号 | 必须 | 必须 |
| Python + pywinpty | 必须 | 不需要 |

`LOGGED_IN=True` 只反映**本机**客户端是否登录。

## 四、CLI 路径怎么找到

按顺序尝试，第一个存在的生效：

| 顺序 | 来源 |
|---|---|
| 1 | `--cli PATH` |
| 2 | 环境变量 `UU_CLI_PATH` |
| 3 | 已保存配置的 `defaults.cli` |
| 4 | 注册表 `HKLM\SOFTWARE\Netease\GameViewerSetup` 的 `InstDir`（以及 WOW6432Node、HKCU 变体） |
| 5 | `PATH` 里的 `uuyc-cli.exe` |
| 6 | 常见安装目录扫描：`<盘>:\`、`\Application`、`\Programs`、`\Netease`、`\Program Files` 下的 `uuRemote\GameViewer\bin`、`GameViewer\bin` 等 |

找不到时报错会列出尝试过的来源。手动固定：

```powershell
$env:UU_CLI_PATH = '<INSTALL_DIR>\bin\uuyc-cli.exe'          # 临时
python scripts/uu-exec.py --cli '<INSTALL_DIR>\bin\uuyc-cli.exe' "hostname"
```

## 五、设备怎么选定

按顺序：

1. `--device ID或名称`（名称支持部分匹配，唯一命中才采用）
2. 环境变量 `UU_DEVICE_ID`
3. 已保存配置的 `defaults.device`
4. 账号下**唯一在线的 Windows 设备**（自动选中并保存）

多个在线设备时报错并列出候选，**不会猜**：

```
ERROR=several online Windows devices, choose one with --device: DESKTOP-A(<DEVICE_ID>), OFFICE-PC(<DEVICE_ID_2>)
```

如果保存的设备离线、而账号下只有一台别的在线 Windows 设备，会自动回退并打印 `WARN=`。

## 六、配置文件

`%APPDATA%\uu-cli\config.json`：

```json
{
  "devices": {
    "<DEVICE_ID>": {
      "password": "dpapi:<base64 blob>",
      "saved_at": "<ISO8601>"
    }
  },
  "defaults": { "device": "<DEVICE_ID>", "cli": "<PATH>" }
}
```

- 密码用 **Windows DPAPI** 加密（绑定当前用户和机器），文件 ACL 用 `icacls` 收紧到当前用户。
- 一台主控可以记住多台被控机，按设备 ID 分开存。
- 早期版本写在 `~/.dsh/uu-cli.json`（明文），首次运行会**自动迁移并删除**旧文件。
- 清除某台设备的密码：`--forget --device <ID>`；删掉整个文件等于全部忘记。
- 想放到别处：设置环境变量 `UU_CLI_CONFIG` 指向你的路径。

## 七、环境变量

| 变量 | 作用 |
|---|---|
| `UU_CLI_PATH` | 覆盖 CLI 路径 |
| `UU_DEVICE_ID` | 覆盖目标设备 |
| `UU_LOCK_PASSWORD` | 提供锁屏密码（优先级低于 `--password` / `--password-stdin`） |
| `UU_CLI_CONFIG` | 覆盖配置文件路径 |

## 八、多台被控机

```powershell
python scripts/uu-exec.py --device <设备名> "hostname"
python scripts/uu-exec.py --device <DEVICE_ID> "hostname"
python scripts/uu-exec.py --check --json     # 看全部设备与在线状态
```

每台设备各自记住自己的密码，互不影响。

## 九、收尾与卸载

```powershell
# 确认没有留下远端会话
python scripts/uu-exec.py --sessions
python scripts/uu-exec.py --kill-all

# 清远端临时文件（正常结束时会自动清，这里只是兜底）
python scripts/uu-exec.py "Remove-Item -Path \$env:TEMP\uu-* -Recurse -Force -EA 0"

# 忘记密码
python scripts/uu-exec.py --forget

# 完全移除：删技能目录 + 配置文件
Remove-Item -Recurse -Force <skill-root>
Remove-Item -Recurse -Force "$env:APPDATA\uu-cli"
```

## 十、安全软件干扰

火绒等实时防护可能把"含 gzip + base64 + 写文件"的脚本判为投放器而**静默删除**。
本 skill 已经把相关片段拆开书写（原因与完整展开见 [file-transfer.md](file-transfer.md)），但不同产品策略不同。

如果脚本消失，请**审阅代码后**对你信任的具体文件放行，或向厂商提交误报。
**不要为了让它跑起来而关闭杀毒软件。**
