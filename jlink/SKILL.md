---
name: jlink
description: >-
  J-Link 下载与在线调试工具，用于探测设备、烧录固件、读写内存、查看寄存器、复位目标和读取 RTT 日志。
  当用户提到 J-Link、JLink、RTT、烧录固件、写内存、读内存、寄存器查看、目标复位、探针连通性检查时自动触发，
  也兼容 /jlink 显式调用。即使用户只是说"烧录一下"或"看看 RTT 输出"，只要上下文涉及 J-Link 探针就应触发此 skill。
argument-hint: "[info|flash|read-mem|write-mem|regs|reset|rtt] ..."
---

# J-Link 下载与在线调试

本 skill 提供 J-Link 探针的设备探测、固件烧录、内存读写、寄存器查看、目标复位和 RTT 日志读取能力。

## 配置

skill 目录下的 `config.json` 包含运行时配置，首次使用前确认 `exe` 路径正确：

```json
{
  "exe": "C:\\Program Files\\SEGGER\\JLink\\JLink.exe",
  "gdbserver_exe": "C:\\Program Files\\SEGGER\\JLink\\JLinkGDBServerCL.exe",
  "rtt_exe": "C:\\Program Files\\SEGGER\\JLink\\JLinkRTTClient.exe",
  "default_device": "",
  "default_interface": "SWD",
  "default_speed": "4000",
  "serial_no": "",
  "rtt_telnet_port": 0,
  "operation_mode": 1
}
```

- `exe`：JLink.exe 完整路径（必填）
- `gdbserver_exe`：JLinkGDBServerCL.exe 路径，RTT 功能需要
- `rtt_exe`：JLinkRTTClient.exe 路径
- `default_device`：默认芯片型号（如 STM32F407VG），为空时需用户指定
- `default_interface`：调试接口，SWD 或 JTAG，默认 SWD
- `default_speed`：调试速率 kHz，默认 4000
- `serial_no`：默认探针序列号，多探针场景下使用
- `rtt_telnet_port`：RTT 端口，0 表示使用工具默认值
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `info` | 探测探针与目标连通性 | 低 |
| `flash` | 烧录固件（.hex / .bin / .elf） | 高 |
| `read-mem` | 读取内存区域 | 低 |
| `write-mem` | 写入内存 | 高 |
| `regs` | 查看 CPU 寄存器 | 低 |
| `reset` | 复位目标芯片 | 高 |
| `rtt` | 读取 RTT 日志输出 | 低 |

## 执行流程

1. 读取 `config.json`，确认 `exe` 路径有效
2. 读取默认 `device / interface / speed / serial_no`
3. 若当前动作需要 `device` 且仍为空，直接要求用户补充，绝不猜测
4. 多探针场景未指定 `serial_no` 时，列出探针让用户选择，不自动选择
5. 按 `operation_mode` 决定是否需要确认后执行
6. 使用模板生成临时 `.jlink` 命令文件，调用 JLink.exe 时带 `-NoGui 1 -ExitOnError 1 -AutoConnect 1`
7. 解析输出和返回码，返回结构化结果
8. `rtt` 子命令先通过 JLinkGDBServerCL.exe 建立调试会话，再启动 RTT 客户端读取输出

## 脚本调用

skill 目录下有两个 Python 脚本，使用标准库实现，无额外依赖。

### jlink_exec.py — 设备探测、烧录、内存读写、寄存器、复位

```bash
# 探测连通性
python <skill-dir>/scripts/jlink_exec.py info --device STM32F407VG --json

# 烧录固件
python <skill-dir>/scripts/jlink_exec.py flash --file build/app.hex --device STM32F407VG --json

# 烧录 .bin（必须提供地址）
python <skill-dir>/scripts/jlink_exec.py flash --file build/app.bin --device STM32F407VG --address 0x08000000 --json

# 读取内存
python <skill-dir>/scripts/jlink_exec.py read-mem --address 0x08000000 --length 256 --device STM32F407VG --json

# 写入内存
python <skill-dir>/scripts/jlink_exec.py write-mem --address 0x20000000 --value 0x12345678 --device STM32F407VG --json

# 查看寄存器
python <skill-dir>/scripts/jlink_exec.py regs --device STM32F407VG --json

# 复位目标
python <skill-dir>/scripts/jlink_exec.py reset --device STM32F407VG --json
```

通用可选参数：`--interface SWD|JTAG`、`--speed 4000`、`--serial-no <序列号>`、`--exe <JLink.exe路径>`

### jlink_rtt.py — RTT 日志读取

```bash
python <skill-dir>/scripts/jlink_rtt.py --device STM32F407VG --json
```

可选参数：`--serial-no`、`--channel`、`--encoding`、`--rtt-port`、`--exe <JLink.exe路径>`、`--gdbserver-exe <路径>`、`--rtt-exe <路径>`

RTT 工作原理：脚本先通过 JLinkGDBServerCL.exe 建立调试连接（保持探针连接活跃），再启动 JLinkRTTClient.exe 读取 RTT 数据。`--json` 模式输出 JSON Lines。

## 输出格式

所有脚本以 JSON 格式返回，包含 `status`（ok/error）、`action`、`summary`、`details` 字段。

成功示例：
```json
{
  "status": "ok",
  "action": "flash",
  "summary": "烧录成功",
  "details": { "device": "STM32F407VG", "elapsed_ms": 1820, "errorlevel": 0 }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "flash",
  "error": { "code": "cannot_connect_target", "message": "无法连接目标芯片" }
}
```

## 核心规则

- 不自动猜测 `device` 芯片型号，缺失时必须询问用户
- 多探针场景不自动选择探针，必须让用户指定序列号
- `.bin` 文件必须显式提供烧录地址，缺失时报错
- 连接失败时给出排查建议（检查连线、供电、接口类型、速度），不自动尝试更激进参数
- 烧录、写内存、复位在参数完整且用户意图明确时直接执行
- 结果回显中始终包含目标芯片、探针序列号、接口类型和执行动作

## 参考

遇到芯片型号问题时可查阅 `references/common_devices.md`。
