"""J-Link 设备探测、烧录、内存读写、寄存器查看、复位"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# J-Link Commander 命令模板
TEMPLATES = {
    "info": "si {interface}\nspeed {speed}\nconnect\nsleep 200\nexit\n",
    "flash_hex": "si {interface}\nspeed {speed}\nconnect\nloadfile {file}\nr\ng\nexit\n",
    "flash_bin": "si {interface}\nspeed {speed}\nconnect\nloadbin {file},{address}\nr\ng\nexit\n",
    "read_mem": "si {interface}\nspeed {speed}\nconnect\nmem{width} {address},{length}\nexit\n",
    "write_mem": "si {interface}\nspeed {speed}\nconnect\nw{width} {address},{value}\nexit\n",
    "regs": "si {interface}\nspeed {speed}\nconnect\nregs\nexit\n",
    "reset": "si {interface}\nspeed {speed}\nconnect\nr\ng\nexit\n",
}

# 错误模式匹配
ERROR_PATTERNS = [
    (r"Cannot connect to target", "cannot_connect_target", "无法连接目标芯片，请检查连线、供电和接口类型"),
    (r"Could not find core", "core_not_found", "未找到内核，请确认 device 是否匹配目标芯片"),
    (r"No J-Link found", "no_jlink_found", "未检测到 J-Link 探针，请确认 USB 连接和驱动"),
    (r"Multiple J-Links found", "multiple_jlinks", "检测到多个 J-Link 探针，请通过 --serial-no 指定序列号"),
    (r"Could not open file", "file_not_found", "无法打开固件文件，请确认路径正确"),
    (r"Unknown device", "unknown_device", "未知芯片型号，请确认 --device 参数"),
    (r"VTarget too low", "vtarget_low", "目标电压过低，请检查目标板供电"),
]


def build_jlink_cmd(exe: str, device: str, script_path: str, serial_no: str = "") -> list:
    """构建 JLink.exe 命令行"""
    cmd = [exe, "-NoGui", "1", "-ExitOnError", "1", "-AutoConnect", "1"]
    cmd.extend(["-Device", device])
    if serial_no:
        cmd.extend(["-SelectEmuBySN", serial_no])
    cmd.extend(["-CommandFile", script_path])
    return cmd


def parse_output(stdout: str, action: str) -> dict:
    """解析 JLink.exe 输出，提取关键信息"""
    result = {"raw": stdout}

    # 检查错误模式
    for pattern, code, message in ERROR_PATTERNS:
        if re.search(pattern, stdout, re.IGNORECASE):
            return {"error_code": code, "error_message": message, "raw": stdout}

    # info: 提取固件版本和目标信息
    if action == "info":
        fw = re.search(r"Firmware:\s+(.+)", stdout)
        sn = re.search(r"S/N:\s+(\d+)", stdout)
        vtarget = re.search(r"VTref=(\d+\.\d+)V", stdout)
        device_match = re.search(r"Device \"(.+?)\" selected", stdout)
        if fw:
            result["firmware"] = fw.group(1).strip()
        if sn:
            result["serial_no"] = sn.group(1).strip()
        if vtarget:
            result["vtarget_v"] = float(vtarget.group(1))
        if device_match:
            result["device"] = device_match.group(1)

    # flash: 提取烧录信息
    elif action == "flash":
        speed = re.search(r"Downloading\s+\d+\s+bytes?\s.*?(\d+\.\d+)\s*KB/s", stdout)
        if speed:
            result["speed_kbps"] = float(speed.group(1))
        if "O.K." in stdout or "Verify successful" in stdout or "Download verified successfully" in stdout:
            result["verified"] = True

    # read-mem: 提取内存数据
    elif action == "read-mem":
        # 匹配 hex dump 行: 08000000 = 20020000 08005BED ...
        mem_lines = re.findall(r"([0-9A-Fa-f]{8})\s*=\s*((?:[0-9A-Fa-f]+\s*)+)", stdout)
        if mem_lines:
            result["memory"] = []
            for addr, data in mem_lines:
                result["memory"].append({"address": f"0x{addr}", "data": data.strip()})

    # regs: 提取寄存器值
    elif action == "regs":
        reg_lines = re.findall(r"(\w+)\s*=\s*([0-9A-Fa-f]{8})", stdout)
        if reg_lines:
            result["registers"] = {name: f"0x{val}" for name, val in reg_lines}

    return result


def run_jlink(exe: str, device: str, action: str, interface: str = "SWD",
              speed: str = "4000", serial_no: str = "", file: str = "",
              address: str = "", length: str = "256", value: str = "",
              width: str = "32") -> dict:
    """执行 JLink Commander 命令"""
    start_time = time.time()

    # 选择模板
    if action == "flash":
        if file.lower().endswith(".bin"):
            if not address:
                return {
                    "status": "error",
                    "action": action,
                    "error": {"code": "missing_address", "message": ".bin 文件必须提供 --address 烧录地址"},
                }
            template = TEMPLATES["flash_bin"]
        else:
            template = TEMPLATES["flash_hex"]
    elif action in TEMPLATES:
        template = TEMPLATES[action]
    else:
        return {
            "status": "error",
            "action": action,
            "error": {"code": "unknown_action", "message": f"未知子命令: {action}"},
        }

    # width 映射
    width_map = {"8": "8", "16": "16", "32": "32"}
    w = width_map.get(width, "32")

    # 渲染命令脚本
    script_content = template.format(
        interface=interface, speed=speed, file=file,
        address=address, length=length, value=value, width=w,
    )

    # 写入临时文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jlink", delete=False, encoding="utf-8") as f:
        f.write(script_content)
        script_path = f.name

    try:
        if not os.path.isfile(exe):
            return {
                "status": "error",
                "action": action,
                "error": {"code": "exe_not_found", "message": f"JLink.exe 不存在: {exe}"},
            }

        if file and not os.path.isfile(file):
            return {
                "status": "error",
                "action": action,
                "error": {"code": "file_not_found", "message": f"固件文件不存在: {file}"},
            }

        cmd = build_jlink_cmd(exe, device, script_path, serial_no)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace"
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "timeout", "message": "JLink.exe 执行超时(120s)"},
            }
        except Exception as e:
            return {
                "status": "error",
                "action": action,
                "error": {"code": "exec_error", "message": str(e)},
            }

        elapsed_ms = int((time.time() - start_time) * 1000)
        parsed = parse_output(proc.stdout, action)

        if "error_code" in parsed:
            return {
                "status": "error",
                "action": action,
                "error": {"code": parsed["error_code"], "message": parsed["error_message"]},
                "details": {"device": device, "elapsed_ms": elapsed_ms, "errorlevel": proc.returncode},
            }

        # 构建摘要
        summary_map = {
            "info": "探测成功",
            "flash": "烧录成功",
            "read-mem": "内存读取成功",
            "write-mem": "内存写入成功",
            "regs": "寄存器读取成功",
            "reset": "复位成功",
        }
        summary = summary_map.get(action, "执行成功")

        details = {
            "device": device,
            "interface": interface,
            "speed_khz": int(speed),
            "elapsed_ms": elapsed_ms,
            "errorlevel": proc.returncode,
        }
        if serial_no:
            details["serial_no"] = serial_no

        # 合并解析结果
        for k, v in parsed.items():
            if k != "raw":
                details[k] = v

        # 判断状态: returncode!=0 可能只是警告，需结合输出判断
        if proc.returncode != 0 and "error_code" not in parsed:
            # 有些 JLink 命令返回非零但实际成功，检查输出
            if action == "flash" and parsed.get("verified"):
                status = "ok"
            else:
                status = "error"
                summary = f"执行返回非零退出码: {proc.returncode}"
        else:
            status = "ok"

        return {
            "status": status,
            "action": action,
            "summary": summary,
            "details": details,
        }
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="J-Link 设备探测/烧录/内存读写/寄存器/复位")
    parser.add_argument("action", choices=["info", "flash", "read-mem", "write-mem", "regs", "reset"])
    parser.add_argument("--exe", default="", help="JLink.exe 路径")
    parser.add_argument("--device", required=True, help="芯片型号（如 STM32F407VG）")
    parser.add_argument("--interface", default="SWD", choices=["SWD", "JTAG"], help="调试接口")
    parser.add_argument("--speed", default="4000", help="调试速率 kHz")
    parser.add_argument("--serial-no", default="", help="探针序列号")
    parser.add_argument("--file", default="", help="固件文件路径（flash 用）")
    parser.add_argument("--address", default="", help="地址（flash .bin / read-mem / write-mem 用）")
    parser.add_argument("--length", default="256", help="读取长度（read-mem 用）")
    parser.add_argument("--value", default="", help="写入值（write-mem 用）")
    parser.add_argument("--width", default="32", choices=["8", "16", "32"], help="数据宽度")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # flash 必须提供 file
    if args.action == "flash" and not args.file:
        result = {
            "status": "error",
            "action": "flash",
            "error": {"code": "missing_file", "message": "flash 必须提供 --file 固件文件路径"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    # write-mem 必须提供 address 和 value
    if args.action == "write-mem" and (not args.address or not args.value):
        result = {
            "status": "error",
            "action": "write-mem",
            "error": {"code": "missing_params", "message": "write-mem 必须提供 --address 和 --value"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    # read-mem 必须提供 address
    if args.action == "read-mem" and not args.address:
        result = {
            "status": "error",
            "action": "read-mem",
            "error": {"code": "missing_address", "message": "read-mem 必须提供 --address"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    result = run_jlink(
        exe=args.exe,
        device=args.device,
        action=args.action,
        interface=args.interface,
        speed=args.speed,
        serial_no=args.serial_no,
        file=args.file,
        address=args.address,
        length=args.length,
        value=args.value,
        width=args.width,
    )

    if args.as_json:
        output_json(result)
    else:
        if result["status"] == "ok":
            print(f"[{args.action}] {result.get('summary', '成功')}")
            details = result.get("details", {})
            print(f"  芯片: {details.get('device', 'N/A')}")
            print(f"  耗时: {details.get('elapsed_ms', 0)}ms")
            if "registers" in details:
                for name, val in details["registers"].items():
                    print(f"  {name} = {val}")
            if "memory" in details:
                for m in details["memory"]:
                    print(f"  {m['address']}: {m['data']}")
        else:
            err = result.get("error", {})
            print(f"[{args.action}] 失败 — {err.get('message', '未知错误')}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
