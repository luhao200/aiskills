---
name: keil
description: >-
  Keil MDK 工程构建与烧录工具，用于扫描 .uvprojx/.uvmpw 工程、枚举 Target、执行
  build/rebuild/clean/flash 并解析构建日志。当用户提到 Keil、MDK、uVision、UV4、
  Target 枚举、编译、重建、清理、烧录、下载固件、flash 时自动触发，也兼容 /keil 显式调用。
  即使用户只是说"编译一下"或"烧录到板子上"，只要上下文涉及嵌入式 Keil 工程就应触发此 skill。
argument-hint: "[scan|targets|build|rebuild|clean|flash] ..."
---

# Keil MDK 工程构建与烧录

本 skill 提供 Keil MDK 工程的发现、Target 枚举、构建、重建、清理和烧录能力。

## 配置

skill 目录下的 `config.json` 包含运行时配置，首次使用前确认 `uv4_exe` 路径正确：

```json
{
  "uv4_exe": "C:\\Keil_v5\\UV4\\UV4.exe",
  "default_target": "",
  "log_dir": ".build",
  "operation_mode": 1
}
```

- `uv4_exe`：UV4.exe 完整路径（必填）
- `default_target`：默认 Target，为空时需用户指定或从工程中选择
- `log_dir`：构建日志输出目录，默认 `.build`
- `operation_mode`：`1` 直接执行 / `2` 输出风险摘要但不阻塞 / `3` 执行前确认

## 子命令

| 子命令 | 用途 | 风险 |
|--------|------|------|
| `scan` | 搜索当前目录下的 .uvprojx/.uvmpw 工程 | 低 |
| `targets` | 枚举工程中的 Target | 低 |
| `build` | 增量编译 | 中 |
| `rebuild` | 全量重建 | 中 |
| `clean` | 清理工程 | 高 |
| `flash` | 通过 Keil 烧录固件 | 高 |

## 执行流程

1. 读取 `config.json`，确认 `uv4_exe` 路径有效
2. 未指定子命令时默认执行 `scan`
3. 未提供工程路径时先执行 `scan` 搜索工程
4. 同时发现多个工程或多个 Target 时，列出选项让用户选择，绝不自动猜测
5. `build/rebuild/clean` 按 `operation_mode` 决定是否需要确认
6. `flash` 仅在最近一次构建成功时允许执行
7. 所有构建命令输出到日志文件后解析，返回结构化结果

## 脚本调用

skill 目录下有两个 Python 脚本，使用标准库实现，无额外依赖。

### keil_project.py — 工程扫描与 Target 枚举

```bash
# 扫描工程
python <skill-dir>/scripts/keil_project.py scan --root <搜索目录> --json

# 枚举 Target
python <skill-dir>/scripts/keil_project.py targets --project <工程路径> --json
```

### keil_build.py — 构建 / 重建 / 清理 / 烧录

```bash
python <skill-dir>/scripts/keil_build.py <build|rebuild|clean|flash> \
  --uv4 <UV4路径> \
  --project <工程路径> \
  --target <TargetName> \
  --log-dir <日志目录> \
  --json
```

`rebuild` 额外支持 `--clean-first` 使用 `-cr` 而非 `-r`。

## 输出格式

所有脚本以 JSON 格式返回，包含 `status`（ok/error）、`action`、`summary`、`details` 字段。

成功示例：
```json
{
  "status": "ok",
  "action": "build",
  "summary": { "errors": 0, "warnings": 2, "flash_bytes": 32768, "ram_bytes": 8192 },
  "details": { "project": "project.uvprojx", "target": "Debug", "log_file": ".build/project-Debug-build.log" }
}
```

错误示例：
```json
{
  "status": "error",
  "action": "flash",
  "error": { "code": "build_not_clean", "message": "最近一次构建存在错误，禁止继续烧录" }
}
```

## 核心规则

- 不修改工程配置文件（.uvprojx / .uvmpw / .uvoptx）
- 不自动猜测工程路径或 Target，有歧义时必须询问用户
- `flash` 前必须确认最近一次构建成功（errors == 0）
- `clean` 不在自动流程中隐式执行
- 构建失败时优先展示首个错误和日志文件路径
- 结果回显中始终包含工程名、Target 名、日志路径

## 参考

遇到编译器相关问题时可查阅 `references/compiler-notes.md`。
