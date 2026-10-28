# 系统操作：进程、服务、端口、文件系统

这类操作**锁屏也能用**——它们不需要交互桌面。是远端排障最常用的一类。

所有命令都通过 `uu-exec.py` 执行。注意单段命令 ≤215 字符，长命令会被自动拆分或需要推脚本。

## 一、进程

```powershell
# 按 CPU 排序前 20
python scripts/uu-exec.py "Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 Name,Id,CPU"
```

```powershell
# 找特定进程
python scripts/uu-exec.py "Get-Process | Where-Object Name -like '*chrome*' | Select-Object Name,Id"

# 进程总数
python scripts/uu-exec.py "@(Get-Process).Count"
```

启动与停止（**会改远端状态，先取得授权**）：

```powershell
python scripts/uu-exec.py "Start-Process 'C:\path\app.exe' -ArgumentList '/flag'"
python scripts/uu-exec.py "Stop-Process -Name 'notepad' -Force"
```

## 二、服务

```powershell
# 正在运行的服务
python scripts/uu-exec.py "Get-Service | Where-Object Status -eq Running | Select-Object -First 20 Name,DisplayName"

# 某个服务的状态
python scripts/uu-exec.py "(Get-Service 'Spooler').Status"

# 运行中服务数量
python scripts/uu-exec.py "@(Get-Service | Where-Object Status -eq Running).Count"
```

改状态（需授权）：

```powershell
python scripts/uu-exec.py "Restart-Service 'Spooler' -Force"
```

## 三、端口与网络

```powershell
# 监听中的端口
python scripts/uu-exec.py "Get-NetTCPConnection -State Listen | Sort-Object LocalPort | Select-Object -First 30 LocalPort,OwningProcess"

# 某个端口被谁占用
python scripts/uu-exec.py "Get-NetTCPConnection -LocalPort 3389 -EA 0 | Select-Object LocalAddress,State,OwningProcess"

# 网络配置
python scripts/uu-exec.py "Get-NetIPAddress -AddressFamily IPv4 | Select-Object IPAddress,InterfaceAlias"
```

## 四、文件系统

```powershell
# 磁盘空间
python scripts/uu-exec.py "Get-PSDrive -PSProvider FileSystem | Select-Object Name,@{n='FreeGB';e={[int]($_.Free/1GB)}},@{n='UsedGB';e={[int]($_.Used/1GB)}}"

# 列目录（大目录先限数量）
python scripts/uu-exec.py "Get-ChildItem 'C:\path' | Select-Object -First 20 Name,Length,LastWriteTime"

# 找文件
python scripts/uu-exec.py "Get-ChildItem 'C:\path' -Recurse -Filter '*.log' -EA 0 | Select-Object -First 20 FullName"

# 文件哈希
python scripts/uu-exec.py "(Get-FileHash 'C:\path\file' -Algorithm SHA256).Hash"

# 路径是否存在
python scripts/uu-exec.py "Test-Path 'C:\path\x'"
```

写文件、删文件（**需授权**）：

```powershell
python scripts/uu-exec.py "Set-Content -Path 'C:\path\a.txt' -Value 'hello' -Encoding UTF8"
python scripts/uu-exec.py "Remove-Item 'C:\path\a.txt' -Force"
```

## 五、系统信息

```powershell
python scripts/uu-exec.py "Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,LastBootUpTime"
python scripts/uu-exec.py "Get-CimInstance Win32_ComputerSystem | Select-Object Name,Manufacturer,TotalPhysicalMemory"
python scripts/uu-exec.py "Get-Volume | Select-Object DriveLetter,FileSystemLabel,@{n='FreeGB';e={[int]($_.SizeRemaining/1GB)}}"
```

## 六、输出太大的处理

通道对单条输出也有上限（外层 base64 约 300–500 字符就开始不稳）。大结果按顺序处理：

1. **缩小查询**：`Select-Object -First N`、只取需要的属性、`Where-Object` 过滤；
2. **远端落文件**：`... | Out-File C:\Users\Public\out.txt`；
3. **再拉回来**：`--pull`（≤5KB 最稳，上限 64KB）。

```powershell
python scripts/uu-exec.py "Get-Process | Select-Object Name,Id | Out-File \$env:TEMP\procs.txt -Encoding UTF8"
python scripts/uu-exec.py --pull "%TEMP%\procs.txt" .\procs.txt
```

## 七、慢命令

装包、编译、大目录扫描很容易超过默认 20 秒超时：

```powershell
python scripts/uu-exec.py --timeout 300 "winget install --id Git.Git -e --silent"
```

`no marker within 20s` 常常只是命令还没跑完，先调 `--timeout` 而不是反复重试。

## 八、纪律

- 只动本次任务相关的东西，删除前先列一遍。
- 改服务、杀进程、装软件、改注册表前取得用户授权。
- 临时文件写到远端 `%TEMP%`，用完删掉（不要留在 `C:\Users\Public`）。
