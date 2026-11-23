"""Command-line interface (also used by run_app.py when a demo is given)."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import webbrowser
from pathlib import Path
from typing import Sequence

from .app import describe_failure, process_demo
from .config import APP_NAME, APP_VERSION, TARGET_DEFAULT
from .demo_parser import ParseError
from .logging_setup import configure_logging, log_file
from .validation import ValidationError, ValidationFailed

LOGGER = logging.getLogger(__name__)


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="CS2Stats",
        description=f"{APP_NAME} {APP_VERSION} — CS2 demo 数据统计与 HTML 报告",
    )
    parser.add_argument("demo", nargs="?", help="要解析的 .dem 文件；省略时打开图形界面")
    parser.add_argument("--out", help="输出 HTML 路径（默认：demo 同目录 report.html）")
    parser.add_argument(
        "--target",
        default=TARGET_DEFAULT,
        help=f"Player performance overview 的目标选手名（默认 {TARGET_DEFAULT}）",
    )
    parser.add_argument("--fixture", help="计分板 fixture JSON 路径；省略时自动在 fixtures/ 中按 sha256 查找")
    parser.add_argument(
        "--allow-unvalidated-preview",
        action="store_true",
        help="开发/排查用：校验不通过仍生成带红色未校验横幅的 HTML（不要作为最终数据）",
    )
    parser.add_argument("--open", action="store_true", help="生成后自动打开报告（demo 模式）")
    parser.add_argument(
        "--watch",
        nargs="?",
        const="",
        default=None,
        metavar="DEMO_DIR",
        help=(
            "监听 GOTV demo 输出文件夹（省略路径时自动找 game\\csgo）；"
            "新 demo 稳定后自动解析并打开报告"
        ),
    )
    parser.add_argument("--watch-out", help="自动监听模式的报告输出目录（默认 文档\\CS2Stats）")
    parser.add_argument(
        "--watch-stable",
        type=float,
        default=3.0,
        help="文件大小稳定多少秒后解析（默认 3.0）",
    )
    parser.add_argument(
        "--watch-once",
        action="store_true",
        help="只处理启动时最新的一个 demo 后退出（用于测试/手动补跑）",
    )
    parser.add_argument(
        "--watch-state",
        help="已处理文件状态 JSON 路径（默认 %%LOCALAPPDATA%%\\CS2StatsReport\\watcher_state.json）",
    )
    parser.add_argument(
        "--require-fixture",
        action="store_true",
        help="手动 Demo 或自动监听都要求计分板 fixture；默认没有 fixture 时生成带“未校验”横幅的报告",
    )
    parser.add_argument(
        "--gsi",
        action="store_true",
        help="启动 GSI 实时监听（实验功能，不解析 demo 参数）",
    )
    parser.add_argument("--gsi-out", help="实时报告输出目录（默认 文档\\CS2Stats）")
    parser.add_argument(
        "--gsi-demo-dir",
        action="append",
        default=[],
        help="实时模式搜索本场 demo 的目录，可重复；省略时搜索默认录制目录",
    )
    parser.add_argument("--gsi-port", type=int, default=32123, help="GSI 监听端口（默认 32123）")
    parser.add_argument("--no-open", action="store_true", help="自动监听/实时模式生成后不自动打开浏览器")
    parser.add_argument("--json", action="store_true", help="把校验/结果摘要以 JSON 打印到 stdout")
    parser.add_argument("--no-cache", action="store_true", help="跳过解析缓存，强制重新解析 demo")
    parser.add_argument("--clear-cache", action="store_true", help="清除全部解析缓存后退出")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.clear_cache:
        from .cache import clear

        removed = clear()
        print(f"已清除 {removed} 个解析缓存。")
        return 0
    if args.no_cache:
        import os

        os.environ["CS2STATS_NO_CACHE"] = "1"

    if args.watch is None and args.watch_once:
        args.watch = ""
    if args.watch is not None or args.gsi or args.demo:
        from .first_run import initialize_quietly

        initialize_quietly()

    if args.watch is not None:
        if args.demo:
            parser.error("--watch 不能和 demo 文件同时使用")
        if args.gsi:
            parser.error("--watch 不能和 --gsi 同时使用")
        return _run_watch(args)

    if args.gsi:
        if args.demo:
            parser.error("--gsi 不能和 demo 文件同时使用")
        return _run_gsi(args)

    if not args.demo:
        from .gui import main as gui_main

        return gui_main()

    configure_logging()
    LOGGER.info("CLI start demo=%s target=%s out=%s", args.demo, args.target, args.out)

    def progress(stage: str, message: str) -> None:
        print(message, file=sys.stderr if args.json else sys.stdout)

    try:
        result = process_demo(
            args.demo,
            out_path=args.out,
            target=args.target,
            fixture_path=args.fixture,
            allow_missing_fixture=not args.require_fixture,
            allow_unvalidated_preview=args.allow_unvalidated_preview,
            progress=progress,
        )
    except ValidationFailed as exc:
        print(describe_failure(exc.result), file=sys.stderr)
        print(f"\n日志：{log_file()}", file=sys.stderr)
        if args.json:
            print(json.dumps(exc.result.report_payload(), ensure_ascii=False, indent=2))
        return 2
    except (ParseError, ValidationError) as exc:
        LOGGER.exception("parse/validation error")
        print(f"错误：{exc}", file=sys.stderr)
        print(f"日志：{log_file()}", file=sys.stderr)
        return 3
    except Exception as exc:  # pragma: no cover - defensive for a windowed app
        LOGGER.exception("unexpected error")
        print(f"未预期的错误：{exc}", file=sys.stderr)
        print(f"日志：{log_file()}", file=sys.stderr)
        return 4

    if args.json:
        print(
            json.dumps(
                {
                    "status": result.validation.status,
                    "title": result.validation.title,
                    "message": result.validation.message,
                    "report": str(result.report_path),
                    "target_found": result.target_found,
                    "target_name": result.target_name,
                    "checks": len(result.validation.checks),
                    "differences": len(result.validation.differences),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"完成：{result.report_path}")
    if args.open:
        try:
            webbrowser.open(result.report_path.resolve().as_uri())
        except Exception:
            LOGGER.warning("could not open browser", exc_info=True)
    return 0


def _run_watch(args: argparse.Namespace) -> int:
    """Watch a demo folder; useful for headless runs and the test suite."""

    from .demo_watch import DemoWatcher, default_output_dir
    from .gotv_config import default_demo_dir

    configure_logging()
    watch_dir = args.watch or ""
    if not watch_dir:
        detected = default_demo_dir()
        if detected is None:
            print(
                "没有自动找到 CS2 的 game\\csgo 目录。请用 --watch <目录> 指定 GOTV demo 输出文件夹。",
                file=sys.stderr,
            )
            return 3
        watch_dir = str(detected)
    out_dir = args.watch_out or str(default_output_dir())
    stream = sys.stderr if args.json else sys.stdout
    last_error: list[str] = []

    def status(message: str) -> None:
        print(message, file=stream)

    def report(result) -> None:
        if args.json:
            print(
                json.dumps(
                    {
                        "status": result.validation.status,
                        "title": result.validation.title,
                        "message": result.validation.message,
                        "demo": str(result.demo.path),
                        "report": str(result.report_path),
                        "target_found": result.target_found,
                        "target_name": result.target_name,
                        "checks": len(result.validation.checks),
                        "differences": len(result.validation.differences),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                file=sys.stdout,
            )
        else:
            print(f"完成：{result.report_path}", file=sys.stdout)

    def error(message: str) -> None:
        last_error.append(message)
        print(f"错误：{message}", file=sys.stderr)

    watcher = DemoWatcher(
        watch_dir,
        out_dir=out_dir,
        target=args.target,
        fixture_path=args.fixture,
        allow_missing_fixture=not args.require_fixture,
        stable_seconds=args.watch_stable,
        open_after=not args.no_open,
        state_file=args.watch_state,
        on_status=status,
        on_report=report,
        on_error=error,
    )
    try:
        if args.watch_once:
            result = watcher.process_latest()
            if result is not None:
                return 0
            return 0 if not last_error else 2
        watcher.start()
        print(
            f"监听中：{watch_dir}\n报告输出：{out_dir}\n"
            "启动时只处理最新一个 .dem；之后每个新 demo 稳定 3 秒后解析。按 Ctrl+C 停止。",
            file=stream,
        )
        watcher.wait()
    except KeyboardInterrupt:
        watcher.stop()
        print("已停止监听。", file=sys.stderr)
        return 130
    except Exception as exc:
        LOGGER.exception("watch failed")
        print(f"监听失败：{exc}", file=sys.stderr)
        print(f"日志：{log_file()}", file=sys.stderr)
        return 3
    finally:
        watcher.stop()
    return 0


def _run_gsi(args: argparse.Namespace) -> int:
    """Foreground live listener used by `--gsi` and headless tests."""

    from .live import LiveSession

    configure_logging()
    session = LiveSession(
        target=args.target,
        out_dir=args.gsi_out,
        demo_dirs=args.gsi_demo_dir or None,
        fixture_path=args.fixture,
        port=args.gsi_port,
        open_after=not args.no_open,
        on_status=lambda message: print(message, file=sys.stderr if args.json else sys.stdout),
        on_error=lambda message: print(f"错误：{message}", file=sys.stderr),
    )
    try:
        session.start()
    except Exception as exc:
        LOGGER.exception("GSI start failed")
        print(f"无法启动 GSI 监听：{exc}", file=sys.stderr)
        print(f"日志：{log_file()}", file=sys.stderr)
        return 3
    print(
        f"GSI 监听中：127.0.0.1:{session.port}。启动 CS2 并完成一局；"
        "正常游玩只有本地玩家，完整 10 人请用观察者模式或 demo 补全。",
        file=sys.stderr if args.json else sys.stdout,
    )
    try:
        result = session.wait()
    except KeyboardInterrupt:
        session.stop()
        print("已停止监听。", file=sys.stderr)
        return 130
    if result is None:
        print("监听已结束，但没有生成报告。", file=sys.stderr)
        return 3
    if args.json:
        print(
            json.dumps(
                {
                    "status": result.status,
                    "title": result.validation.get("title"),
                    "report": str(result.report_path),
                    "target_found": result.target_found,
                    "target_name": result.target_name,
                    "demo_enriched": result.demo is not None,
                    "demo_validated": result.demo_validated,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"完成：{result.report_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
