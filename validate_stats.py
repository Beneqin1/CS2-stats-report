#!/usr/bin/env python
"""Validate a CS2 demo's exact stats against a scoreboard fixture.

Usage (from the project folder, with the bundled .venv):
    .venv\\Scripts\\python.exe validate_stats.py path\\to\\match.dem path\\to\\fixture.json
    .venv\\Scripts\\python.exe validate_stats.py path\\to\\match.dem      # auto-find by sha256
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cs2stats.demo_parser import ParseError, load_demo
from cs2stats.validation import ValidationError, format_result, validate_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CS2 demo 精确字段校验")
    parser.add_argument("demo", help="要校验的 .dem 文件")
    parser.add_argument("fixture", nargs="?", help="计分板 fixture JSON；省略时自动查找")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument("--quiet", action="store_true", help="只显示摘要，不显示逐项表")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    demo_path = Path(args.demo)
    if not demo_path.is_file():
        print(f"找不到 demo：{demo_path}", file=sys.stderr)
        return 2
    try:
        demo = load_demo(demo_path)
    except ParseError as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 2

    try:
        result = validate_demo(demo, args.fixture)
    except ValidationError as exc:
        print(f"fixture 错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.report_payload(), ensure_ascii=False, indent=2))
    else:
        print(format_result(result, verbose=not args.quiet))

    if result.status == "verified":
        return 0
    if result.status == "partial":
        return 1
    if result.status == "mismatch":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
