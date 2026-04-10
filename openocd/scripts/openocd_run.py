"""OpenOCD 探针探测、固件烧录、Flash 擦除、目标复位"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


# 错误模式匹配
ERROR_PATTERNS = [
    (r"Error:\s*open failed", "adapter_open_failed", "调试器打开失败，请检查 USB 连接和驱动"),
    (r"Error:\s*Failed to open device", "adapter_open_failed", "调试器打开失败，请检查 USB 连接和驱动"),
    (r"Error:\s*No.+device found", "no_device", "未找到调试器设备，请检查 USB 连接和驱动"),
    (r"Error:\s*unable to find.+cfg", "cfg_not_found", "未找到指定的配置文件，请确认 cfg 路径"),
    (r"Error:\s*Transport .+ is not selected", "transport_error", "传输协议未选择，请检查 transport 设置"),
    (r"Error:\s*init mode failed", "init_failed", "初始化失败，请检查连线、供电和配置组合"),
    (r"Error:\s*Could not verify flash", "verify_failed", "固件校验失败，Flash 可能损坏或写保护"),
    (r"Error:\s*flash write failed", "flash_write_failed", "Flash 写入失败，请检查固件文件和目标状态"),
    (r"Error:\s*timed out while waiting for target halted", "target_timeout", "等待目标暂停超时，请检查连接"),
    (r"Error:\s*couldn't bind .+ to socket", "port_busy", "端口被占用，请检查是否有其他 OpenOCD 实例运行"),
    (r"Error:\s*Target not examined yet", "target_not_examined", "目标未初始化，请检查 target 配置"),
    (r"Error:\s*flash bank", "flash_bank_error", "Flash bank 配置错误，请确认 target 配置匹配芯片"),
    (r"Error:\s*device is read protected", "read_protected", "芯片读保护已开启，需要先解锁（本工具不自动解锁）"),
    (r"Error:\s*Cannot connect", "cannot_connect", "无法连接目标，请检查连线、供电和接口类型"),
    (r"Error:\s*Could not connect", "cannot_connect", "无法连接目标，请检查连线、供电和接口类型"),
]


def build_openocd_cmd(exe: str, board: str = "", interface: str = "", target: str = "",
                      search: str = "", adapter_speed: str = "", transport: str = "",
                      extra_commands: list = None) -> list:
    """构建 openocd 命令行"""
    cmd = [exe]

    if search:
        cmd.extend(["-s", search])

    # board 优先
    if board:
        cmd.extend(["-f", board])
    else:
        if interface:
            cmd.extend(["-f", interface])
        if target:
            cmd.extend(["-f", target])

    # adapter speed
    if adapter_speed:
        cmd.extend(["-c", f"adapter speed {adapter_speed}"])

    # transport
    if transport:
        cmd.extend(["-c", f"transport select {transport}"])

    # 追加命令
    if extra_commands:
        for c in extra_commands:
            cmd.extend(["-c", c])

    return cmd


def parse_output(combined: str, action: str) -> dict:
    """解析 OpenOCD 输出"""
    result = {"raw": combined}

    # 检查错误模式
    for pattern, code, message in ERROR_PATTERNS:
        if re.search(pattern, combined, re.IGNORECASE):
            return {"error_code": code, "error_message": message, "raw": combined}

    # 通用 Error 检查（放在具体模式后面，作为兜底）
    generic_errors = re.findall(r"Error:\s*(.+)", combined)
    if generic_errors and action != "probe":
        # probe 模式下某些 Error 可能是正常的（如 target 没完全初始化就 shutdown）
        # 其他模式下如果有 Error 且前面没匹配到具体模式，作为通用错误
        pass  # 由调用方根据 returncode 判断

    if action == "probe":
        # 提取 target 信息
        target_match = re.search(r"Info\s*:\s*(\S+\.cm\S*)\s", combined)
        if target_match:
            result["core"] = target_match.group(1)
        # 提取 JTAG tap
        tap_match = re.search(r"Info\s*:\s*JTAG tap:\s*(\S+)", combined)
        if tap_match:
            result["jtag_tap"] = tap_match.group(1)
        # 提取 flash 大小
        flash_match = re.search(r"flash size\s*=\s*(\d+)\s*(\w?)bytes", combined, re.IGNORECASE)
        if flash_match:
            size = int(flash_match.group(1))
            unit = flash_match.group(2).lower()
            if unit == "k":
                size *= 1024
            elif unit == "m":
                size *= 1024 * 1024
            result["flash_size_bytes"] = size

    elif action == "flash":
        # 提取烧录速度
        speed_match = re.search(r"wrote\s+(\d+)\s+bytes.*?in\s+([\d.]+)s\s+\(([\d.]+)\s+KiB/s\)", combined)
        if speed_match:
            result["bytes_written"] = int(speed_match.group(1))
            result["elapsed_s"] = float(speed_match.group(2))
            result["speed_kbps"] = float(speed_match.group(3))
        # 校验
        if "verified OK" in combined or "** Verified OK **" in combined:
            result["verified"] = True
        # 烧录成功标志
        if "** Programming Finished **" in combined:
            result["programmed"] = True

    elif action == "erase":
        if "erased" in combined.lower() or "mass erase complete" in combined.lower():
            result["erased"] = True

    return result


def run_openocd(exe: str, action: str, board: str = "", interface: str = "",
                target: str = "", search: str = "", adapter_speed: str = "",
                transport: str = "", file: str = "", address: str = "",
                reset_mode: str = "run", bank: str = "") -> dict:
    """执行 OpenOCD 命令"""
    start_time = time.time()

    # 参数校验：board 或 interface+target 至少有一个
    if not board and not interface and not target:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "missing_config", "message": "必须提供 --board 或 --interface + --target"},
        }

    # flash 校验
    if action == "flash":
        if not file:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "missing_file", "message": "flash 必须提供 --file 固件文件路径"},
            }
        if file.lower().endswith(".bin") and not address:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "missing_address", "message": ".bin 文件必须提供 --address 烧录地址"},
            }

    # 构建 OpenOCD 命令
    extra_commands = []

    if action == "probe":
        extra_commands = ["init", "targets", "shutdown"]

    elif action == "flash":
        # 根据文件类型选择烧录命令
        file_abs = os.path.abspath(file).replace("\\", "/")
        if file.lower().endswith(".bin"):
            extra_commands = [
                "init",
                f"program {{{file_abs}}} verify reset exit {address}",
            ]
        else:
            # .elf / .hex 不需要地址
            extra_commands = [
                "init",
                f"program {{{file_abs}}} verify reset exit",
            ]

    elif action == "erase":
        bank_idx = bank if bank else "0"
        extra_commands = [
            "init",
            f"flash erase_sector {bank_idx} 0 last",
            "shutdown",
        ]

    elif action == "reset":
        if reset_mode == "halt":
            extra_commands = ["init", "reset halt", "shutdown"]
        elif reset_mode == "init":
            extra_commands = ["init", "reset init", "shutdown"]
        else:
            extra_commands = ["init", "reset run", "shutdown"]

    cmd = build_openocd_cmd(
        exe=exe, board=board, interface=interface, target=target,
        search=search, adapter_speed=adapter_speed, transport=transport,
        extra_commands=extra_commands,
    )

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "exe_not_found", "message": f"openocd 不存在或不在 PATH 中: {exe}"},
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "timeout", "message": "OpenOCD 执行超时(120s)"},
        }
    except Exception as e:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "exec_error", "message": str(e)},
        }

    elapsed_ms = int((time.time() - start_time) * 1000)

    # OpenOCD 的主要输出在 stderr
    combined = proc.stderr + "\n" + proc.stdout
    parsed = parse_output(combined, action)

    if "error_code" in parsed:
        return {
            "status": "error",
            "action": action,
            "error": {"code": parsed["error_code"], "message": parsed["error_message"]},
            "details": {
                "board": board,
                "interface": interface,
                "target": target,
                "elapsed_ms": elapsed_ms,
                "returncode": proc.returncode,
            },
        }

    # 构建摘要
    summary_map = {
        "probe": "目标探测成功",
        "flash": "烧录成功",
        "erase": "Flash 擦除成功",
        "reset": f"复位成功（模式: {reset_mode}）",
    }
    summary = summary_map.get(action, "执行成功")

    # 补充 flash 摘要
    if action == "flash" and "speed_kbps" in parsed:
        summary = f"烧录成功，{parsed['bytes_written']} bytes @ {parsed['speed_kbps']} KiB/s"

    details = {
        "elapsed_ms": elapsed_ms,
        "returncode": proc.returncode,
    }
    if board:
        details["board"] = board
    if interface:
        details["interface"] = interface
    if target:
        details["target"] = target

    # 合并解析结果
    for k, v in parsed.items():
        if k != "raw":
            details[k] = v

    # 判断状态
    if proc.returncode != 0 and "error_code" not in parsed:
        # OpenOCD returncode 非零但没匹配到具体错误
        if action == "flash" and parsed.get("verified"):
            status = "ok"
        elif action == "probe":
            # probe 有时 shutdown 后 returncode 非零但实际成功
            if "jtag_tap" in parsed or "core" in parsed or "flash_size_bytes" in parsed:
                status = "ok"
            else:
                status = "error"
                summary = f"探测失败，返回码: {proc.returncode}"
        else:
            status = "error"
            # 从输出中提取 Error 行作为补充信息
            error_lines = re.findall(r"Error:\s*(.+)", combined)
            if error_lines:
                summary = f"失败: {error_lines[-1].strip()}"
            else:
                summary = f"执行返回非零退出码: {proc.returncode}"
    else:
        status = "ok"

    return {
        "status": status,
        "action": action,
        "summary": summary,
        "details": details,
    }


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


ALL_ACTIONS = ["probe", "flash", "erase", "reset"]


def main():
    parser = argparse.ArgumentParser(description="OpenOCD 探针探测/固件烧录/擦除/复位")
    parser.add_argument("action", choices=ALL_ACTIONS)
    parser.add_argument("--exe", default="openocd", help="openocd 路径")
    parser.add_argument("--board", default="", help="board 配置文件（如 board/stm32f4discovery.cfg）")
    parser.add_argument("--interface", default="", help="interface 配置文件（如 interface/stlink.cfg）")
    parser.add_argument("--target", default="", help="target 配置文件（如 target/stm32f4x.cfg）")
    parser.add_argument("--search", default="", help="额外配置脚本搜索目录")
    parser.add_argument("--adapter-speed", default="", help="调试速率 kHz")
    parser.add_argument("--transport", default="", choices=["", "swd", "jtag"], help="传输协议")
    parser.add_argument("--file", default="", help="固件文件路径（flash 用）")
    parser.add_argument("--address", default="", help="烧录地址（flash .bin 用）")
    parser.add_argument("--mode", default="run", choices=["halt", "run", "init"], help="复位模式（reset 用）")
    parser.add_argument("--bank", default="", help="Flash bank 编号（erase 用，默认 0）")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # 文件存在检查
    if args.action == "flash" and args.file and not os.path.isfile(args.file):
        result = {
            "status": "error",
            "action": "flash",
            "error": {"code": "file_not_found", "message": f"固件文件不存在: {args.file}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    result = run_openocd(
        exe=args.exe,
        action=args.action,
        board=args.board,
        interface=args.interface,
        target=args.target,
        search=args.search,
        adapter_speed=args.adapter_speed,
        transport=args.transport,
        file=args.file,
        address=args.address,
        reset_mode=args.mode,
        bank=args.bank,
    )

    if args.as_json:
        output_json(result)
    else:
        if result["status"] == "ok":
            print(f"[{args.action}] {result.get('summary', '成功')}")
            details = result.get("details", {})
            if "flash_size_bytes" in details:
                print(f"  Flash: {details['flash_size_bytes']} bytes")
            if "core" in details:
                print(f"  Core: {details['core']}")
            if "jtag_tap" in details:
                print(f"  JTAG TAP: {details['jtag_tap']}")
            if "verified" in details:
                print(f"  校验: {'通过' if details['verified'] else '失败'}")
        else:
            err = result.get("error", {})
            print(f"[{args.action}] 失败 — {err.get('message', '未知错误')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
