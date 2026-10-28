# 桌面自动化：UIA、鼠标键盘、窗口、剪贴板、截屏

所有操作都通过 PTY 终端在远端执行 PowerShell 实现，**不需要看到远端画面**。
有远控画面（`device connect`）只是多了"人能同时看见"，自动化代码完全一样。

## 目录

- [一、先判断能不能用](#一先判断能不能用)
- [二、硬规则：含分号的脚本必须推文件](#二硬规则含分号的脚本必须推文件)
- [三、UIA](#三uia)
- [四、鼠标](#四鼠标)
- [五、键盘](#五键盘)
- [六、窗口](#六窗口)
- [七、剪贴板](#七剪贴板)
- [八、截屏](#八截屏)

进程 / 服务 / 端口 / 文件系统操作见 [system-ops.md](system-ops.md)。

## 一、先判断能不能用

```powershell
python scripts/uu-exec.py --desktop
```

输出：

```
SESSION=1
EXPLORER=1
INTERACTIVE=True
LOCKED=True
SCREEN={X=0,Y=0,Width=<W>,Height=<H>}
DESKTOP_READY=False
NOTE=远端处于锁屏（logonui 在运行）：...
```

| 能力 | 锁屏时 | 解锁后 |
|---|---|---|
| 进程 / 服务 / 端口 / 文件系统 | ✅ | ✅ |
| 窗口标题枚举 | ✅ | ✅ |
| UIA 加载与结构枚举 | ✅ | ✅ |
| UIA 元素级操作（Invoke/Value/Select） | ⚠️ 常失败 | ✅ |
| 鼠标 / 键盘 | ✅（会动真实光标） | ✅ |
| 剪贴板**读** | ❌ | ✅ |
| 剪贴板**写** | ⚠️ 调用不报错，但读不回来，无法验证 | ✅ |
| 截屏内容 | ❌ 只有锁屏画面 | ✅ |

**注意：`--desktop` 自己也要建会话。** 远端锁屏且本机没有密码时，它会在登录阶段就失败——
所以它不是严格只读的"先探路"命令。先带一次 `--password-stdin` 把密码记住，再用它。

`DESKTOP_READY=False` 时**不要承诺**剪贴板、截屏内容、UIA 元素级操作。

## 二、硬规则：含分号的脚本必须推文件

单次写入上限是 **215 字符**（含包装后 220），而 `split_command` 只在**顶层** `;` 处拆分。
`Add-Type -TypeDefinition 'using System;...'` 这类 C# 代码里的分号会被当作命令分隔符，
必然把脚本切坏。

```powershell
# 正确做法：先推上去，再执行
python scripts/uu-exec.py --push .\mouse.ps1 C:\Users\Public\mouse.ps1
python scripts/uu-exec.py "powershell -NoProfile -ExecutionPolicy Bypass -File C:\Users\Public\mouse.ps1"
python scripts/uu-exec.py "Remove-Item C:\Users\Public\mouse.ps1 -Force"
```

下面所有 `Add-Type -TypeDefinition` 示例都属于这一类，**不要直接内联**。

## 三、UIA

### 加载与查找

```powershell
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes

$root = [Windows.Automation.AutomationElement]::RootElement
$cond = New-Object Windows.Automation.PropertyCondition(
    [Windows.Automation.AutomationElement]::NameProperty,'记事本')
$win = $root.FindFirst([Windows.Automation.TreeScope]::Children,$cond)
Write-Output "FOUND=$($win -ne $null)"
```

### 常用 Pattern

```powershell
$win.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
$win.GetCurrentPattern([Windows.Automation.ValuePattern]::Pattern).Current.Value
$win.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
$win.GetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern).Expand()
```

### 实战提醒

- **UIA 找到了但点不动**：Qt / 自绘界面常见。`InvokePattern.Invoke()` 和 `PostMessage` 都可能毫无反应。
  这类界面只能靠真实鼠标点击（需要 DPI 感知 + 窗口真正在前台）。遇到就换方案，不要反复试 Invoke。
- **窗口最小化时** UIA 拿不到内容，先还原。
- 远端是 **PowerShell 5.1**，避免 PS7 专属语法（`??`、`?.`、`ForEach-Object -Parallel`）。

## 四、鼠标

```powershell
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Cursor]::Position          # 先记下当前位置，结束时复原
```

移动与点击（脚本推上去执行）：

```powershell
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class M {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, int e);
}
'@
[M]::SetCursorPos(500,300)
[M]::mouse_event(2,0,0,0,0)     # LEFTDOWN
[M]::mouse_event(4,0,0,0,0)     # LEFTUP
```

标志位：`2`=LEFTDOWN `4`=LEFTUP `8`=RIGHTDOWN `16`=RIGHTUP `2048`=WHEEL。
双击 = 两次 down/up，中间 `Start-Sleep -Milliseconds 50`。

**纪律**：鼠标会移动真实光标。操作前记录位置，结束时复原；能不用就不用。

## 五、键盘

```powershell
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.SendKeys]::SendWait('hello{ENTER}')
```

特殊键：`{ENTER}` `{TAB}` `{ESC}` `{BACKSPACE}` `{DELETE}` `{F1}`–`{F12}` `^{A}`（Ctrl+A） `+{TAB}`（Shift+Tab）。

**无副作用验证键盘是否可用**：`SendKeys('{NUMLOCK}')`（切换后再切回）。

## 六、窗口

```powershell
# 列出有标题的窗口
Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object Name,MainWindowTitle
```

置前（同样要推脚本，因为含 Add-Type）：

```powershell
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public class W {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
}
'@
$p = Get-Process notepad | Select-Object -First 1
[W]::SetForegroundWindow($p.MainWindowHandle)
```

前台化常需配合模拟一次 Alt 键绕过前台锁定限制，且进程必须 DPI 感知，否则坐标会偏。

## 七、剪贴板

```powershell
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Clipboard]::SetText('文本')
[Windows.Forms.Clipboard]::GetText()
```

- **锁屏时读一定失败**（安全桌面占用剪贴板）。
- **锁屏时写**：调用通常不报错，但读不回来，**无法验证是否真的生效**——不要把"没报错"当成成功。
- 长文本粘贴套路：`SetText` → `SendKeys('^v')`，同样要求解锁。

## 八、截屏

不要用原图：全尺寸 PNG 有几 MB，通道约 80 B/s。用封装好的入口：

```powershell
python scripts/uu-exec.py --screenshot shot.jpg --width 640 --quality 30 --max-bytes 4000
```

过程：远端在自建的 `%TEMP%\uu-shot-*` 目录里截屏 → 按 `--max-bytes` 反复降采样/降质量
（每轮打印 `SHOT_BYTES=`）→ 走分片拉取 → 清理临时目录。

- 远端原始 JPEG 通常是**几十 KB**；`--max-bytes` 是**传输预算**，不是原图大小。
- 超出预算仍拉不小时会打 `WARN=`，继续拉会很慢。
- 锁屏时截到的只是锁屏画面。

## 九、注意事项汇总

- 单段命令 ≤215 字符 → 复杂脚本推文件再 `-File`。
- 含 `;` 的 C# / 多语句脚本一律推文件。
- 远端 PowerShell 5.1，不用 PS7 语法。
- 会改变远端状态的操作先取得用户授权。
- 用完删掉推上去的临时脚本。
