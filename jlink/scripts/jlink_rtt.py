"""J-Link RTT 日志读取

工作流程：
1. 启动 JLinkGDBServerCL.exe 作为后台进程，建立探针连接并开启 RTT
2. 等待 GDB Server 就绪
3. 启动 JLinkRTTClient.exe 连接到 RTT 端口，持续读取输出
4. Ctrl+C 退出时清理所有子进程
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False), flush=True)


def output_rtt_line(text: str, channel: int = 0, as_json: bool = False):
    """输出一行 RTT 数据"""
    if as_json:
        record = {
            "timestamp": datetime.now().isoformat(),
            "channel": channel,
            "text": text.rstrip(),
        }
        print(json.dumps(record, ensure_ascii=False), flush=True)
    else:
        print(text, end="", flush=True)


def start_gdbserver(gdbserver_exe: str, device: str, interface: str = "SWD",
                    speed: str = "4000", serial_no: str = "", rtt_port: int = 0) -> subprocess.Popen:
    """启动 JLinkGDBServerCL.exe 后台进程"""
    cmd = [
        gdbserver_exe,
        "-device", device,
        "-if", interface,
        "-speed", speed,
        "-noir",       # 不使用交互模式
        "-LocalhostOnly",
        "-nologtofile",
    ]
    if serial_no:
        cmd.extend(["-select", f"USB={serial_no}"])
    if rtt_port:
        cmd.extend(["-RTTTelnetPort", str(rtt_port)])

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc


def wait_gdbserver_ready(proc: subprocess.Popen, timeout: int = 15) -> bool:
    """等待 GDB Server 就绪（检测 'Waiting for GDB connection' 或 'Connected to target'）"""
    start = time.time()
    while time.time() - start < timeout:
        if proc.poll() is not None:
            return False
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.1)
            continue
        # GDB Server 就绪标志
        if "Waiting for GDB connection" in line or "Connected to target" in line or "J-Link is connected" in line:
            return True
        if "Cannot connect" in line or "Could not connect" in line:
            return False
    return False


def start_rtt_client(rtt_exe: str, rtt_port: int = 19021) -> subprocess.Popen:
    """启动 JLinkRTTClient.exe"""
    cmd = [rtt_exe]
    if rtt_port:
        cmd.extend(["-LocalEcho", "Off", "-RTTTelnetPort", str(rtt_port)])
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc


def cleanup(procs: list):
    """清理所有子进程"""
    for proc in procs:
        if proc and proc.poll() is None:
            try:
                if sys.platform == "win32":
                    proc.terminate()
                else:
                    proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                proc.kill()


def main():
    parser = argparse.ArgumentParser(description="J-Link RTT 日志读取")
    parser.add_argument("--device", required=True, help="芯片型号")
    parser.add_argument("--exe", default="", help="JLink.exe 路径（未使用，保持接口一致）")
    parser.add_argument("--gdbserver-exe", default="", help="JLinkGDBServerCL.exe 路径")
    parser.add_argument("--rtt-exe", default="", help="JLinkRTTClient.exe 路径")
    parser.add_argument("--interface", default="SWD", choices=["SWD", "JTAG"])
    parser.add_argument("--speed", default="4000", help="调试速率 kHz")
    parser.add_argument("--serial-no", default="", help="探针序列号")
    parser.add_argument("--channel", type=int, default=0, help="RTT 通道")
    parser.add_argument("--encoding", default="utf-8", help="输出编码")
    parser.add_argument("--rtt-port", type=int, default=0, help="RTT Telnet 端口")
    parser.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    # 检查必要工具
    if not args.gdbserver_exe or not os.path.isfile(args.gdbserver_exe):
        result = {
            "status": "error",
            "action": "rtt",
            "error": {"code": "gdbserver_not_found",
                      "message": f"JLinkGDBServerCL.exe 不存在: {args.gdbserver_exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    if not args.rtt_exe or not os.path.isfile(args.rtt_exe):
        result = {
            "status": "error",
            "action": "rtt",
            "error": {"code": "rtt_exe_not_found",
                      "message": f"JLinkRTTClient.exe 不存在: {args.rtt_exe}"},
        }
        if args.as_json:
            output_json(result)
        else:
            print(f"错误: {result['error']['message']}", file=sys.stderr)
        sys.exit(1)

    procs = []
    try:
        # 步骤 1: 启动 GDB Server
        if not args.as_json:
            print("正在启动 JLinkGDBServerCL ...", file=sys.stderr, flush=True)

        gdb_proc = start_gdbserver(
            args.gdbserver_exe, args.device, args.interface,
            args.speed, args.serial_no, args.rtt_port,
        )
        procs.append(gdb_proc)

        # 步骤 2: 等待就绪
        if not wait_gdbserver_ready(gdb_proc):
            stderr_out = ""
            if gdb_proc.poll() is not None:
                stderr_out = gdb_proc.stderr.read()
            result = {
                "status": "error",
                "action": "rtt",
                "error": {"code": "gdbserver_failed",
                          "message": f"GDB Server 启动失败或连接超时。{stderr_out}".strip()},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {result['error']['message']}", file=sys.stderr)
            cleanup(procs)
            sys.exit(1)

        if not args.as_json:
            print("GDB Server 就绪，启动 RTT Client ...", file=sys.stderr, flush=True)

        # 步骤 3: 启动 RTT Client
        rtt_port = args.rtt_port if args.rtt_port else 19021
        rtt_proc = start_rtt_client(args.rtt_exe, rtt_port)
        procs.append(rtt_proc)

        # 步骤 4: 持续读取 RTT 输出
        if not args.as_json:
            print("RTT 输出开始（Ctrl+C 退出）:", file=sys.stderr, flush=True)
            print("-" * 40, file=sys.stderr, flush=True)

        while True:
            line = rtt_proc.stdout.readline()
            if not line:
                if rtt_proc.poll() is not None:
                    break
                time.sleep(0.01)
                continue
            # 过滤 RTTClient 自身 banner 和状态信息
            stripped = line.strip()
            if (stripped.startswith("###RTT Client:") or
                    stripped.startswith("SEGGER J-Link") or
                    stripped.startswith("Process:") or
                    stripped == "" or
                    stripped.startswith("***") or
                    stripped.startswith("---")):
                continue
            output_rtt_line(line, args.channel, args.as_json)

    except KeyboardInterrupt:
        if not args.as_json:
            print("\n已停止 RTT 读取", file=sys.stderr, flush=True)
    finally:
        cleanup(procs)


if __name__ == "__main__":
    main()
