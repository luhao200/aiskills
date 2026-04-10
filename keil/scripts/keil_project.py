"""Keil MDK 工程扫描与 Target 枚举"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def scan_projects(root: str) -> list[dict]:
    """递归搜索 .uvprojx 和 .uvmpw 文件"""
    root_path = Path(root).resolve()
    projects = []
    for ext in ("*.uvprojx", "*.uvmpw"):
        for p in root_path.rglob(ext):
            projects.append({
                "path": str(p),
                "name": p.stem,
                "type": "workspace" if p.suffix == ".uvmpw" else "project",
            })
    projects.sort(key=lambda x: x["path"])
    return projects


def list_targets(project_path: str) -> list[dict]:
    """解析 .uvprojx 中的 TargetName"""
    p = Path(project_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"工程文件不存在: {p}")
    if p.suffix != ".uvprojx":
        raise ValueError(f"仅支持 .uvprojx 文件，当前: {p.suffix}")

    tree = ET.parse(str(p))
    root = tree.getroot()
    targets = []
    for target_el in root.iter("Target"):
        name_el = target_el.find("TargetName")
        if name_el is not None and name_el.text:
            targets.append({"name": name_el.text.strip()})
    return targets


def output_json(data: dict):
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Keil 工程扫描与 Target 枚举")
    sub = parser.add_subparsers(dest="command")

    scan_p = sub.add_parser("scan", help="搜索工程文件")
    scan_p.add_argument("--root", default=".", help="搜索根目录")
    scan_p.add_argument("--json", action="store_true", dest="as_json")

    targets_p = sub.add_parser("targets", help="枚举 Target")
    targets_p.add_argument("--project", required=True, help="工程文件路径")
    targets_p.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args()

    if args.command == "scan":
        projects = scan_projects(args.root)
        result = {
            "status": "ok",
            "action": "scan",
            "details": {"projects": projects, "count": len(projects)},
        }
        if args.as_json:
            output_json(result)
        else:
            if not projects:
                print("未找到 Keil 工程文件")
            else:
                print(f"找到 {len(projects)} 个工程：")
                for p in projects:
                    print(f"  [{p['type']}] {p['path']}")

    elif args.command == "targets":
        try:
            targets = list_targets(args.project)
            result = {
                "status": "ok",
                "action": "targets",
                "details": {
                    "project": args.project,
                    "targets": targets,
                    "count": len(targets),
                },
            }
            if args.as_json:
                output_json(result)
            else:
                if not targets:
                    print("未找到 Target")
                else:
                    print(f"工程 {args.project} 包含 {len(targets)} 个 Target：")
                    for t in targets:
                        print(f"  - {t['name']}")
        except (FileNotFoundError, ValueError) as e:
            result = {
                "status": "error",
                "action": "targets",
                "error": {"code": "invalid_project", "message": str(e)},
            }
            if args.as_json:
                output_json(result)
            else:
                print(f"错误: {e}", file=sys.stderr)
            sys.exit(1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
