"""End-to-end pipeline: demo -> validation -> report data -> HTML."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import TARGET_DEFAULT
from .demo_parser import ParsedDemo, ParseError, load_demo
from .paths import output_dir as app_output_dir
from .render import render_report
from .report_data import build_report
from .validation import ValidationFailed, ValidationResult, validate_demo

LOGGER = logging.getLogger(__name__)
Progress = Callable[[str, str], None]


@dataclass
class ProcessResult:
    demo: ParsedDemo
    report_path: Path
    validation: ValidationResult
    target_found: bool
    target_name: str | None


def _noop(stage: str, message: str) -> None:
    LOGGER.info("[%s] %s", stage, message)


def process_demo(
    demo_path: str | Path,
    *,
    out_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    target: str = TARGET_DEFAULT,
    fixture_path: str | Path | None = None,
    allow_unvalidated_preview: bool = False,
    allow_missing_fixture: bool = False,
    progress: Progress | None = None,
) -> ProcessResult:
    """Parse, validate and render one demo.

    In the default (strict) mode a report is produced only when every exact
    field matches the fixture.  ``allow_missing_fixture`` is the auto-watcher
    path: a demo with no fixture still produces a report, clearly banner-marked
    as unverified, but a fixture that is present and disagrees still aborts.
    ``allow_unvalidated_preview`` is a deliberately loud escape hatch for
    development; it also continues after a mismatch.
    """

    notify = progress or _noop
    demo_path = Path(demo_path)
    if not demo_path.is_file():
        raise ParseError(f"找不到 demo 文件：{demo_path}")
    if demo_path.suffix.lower() != ".dem":
        raise ParseError(f"文件扩展名不是 .dem：{demo_path.name}")

    notify("parse", f"正在解析 {demo_path.name}……")
    demo = load_demo(demo_path)
    if getattr(demo, "from_cache", False):
        notify("parse", "使用缓存的解析结果（demo 未改动，跳过重复解析）。")
    notify("parse", f"解析完成：{demo.map_name}，{len(demo.counted_rounds)} 个正式回合，{len(demo.players)} 名选手。")

    notify("validate", "正在用计分板 fixture 校验精确字段……")
    result = validate_demo(demo, fixture_path)
    LOGGER.info("validation status=%s checks=%d differences=%d", result.status, len(result.checks), len(result.differences))
    if result.status == "verified":
        notify("validate", result.message)
    elif allow_missing_fixture and result.status == "unverified":
        notify(
            "validate",
            "未找到计分板 fixture；按自动监听模式继续。报告会标记为“未校验”，"
            "不会宣称与计分板逐项一致。",
        )
    elif allow_unvalidated_preview:
        notify(
            "validate",
            f"警告：{result.title}。已按 --allow-unvalidated-preview 继续，报告会显示红色未校验横幅。",
        )
    else:
        raise ValidationFailed(result)

    notify("report", "正在构建报告数据……")
    report = build_report(demo, target_name=target, validation=result.report_payload())
    if out_path:
        output = Path(out_path)
    else:
        output_dir = Path(out_dir) if out_dir else app_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_map = re.sub(r"[^A-Za-z0-9_-]+", "_", demo.map_name or "map").strip("_") or "map"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = output_dir / f"report_{safe_map}_{stamp}.html"
        serial = 2
        while output.exists():
            output = output_dir / f"report_{safe_map}_{stamp}_{serial}.html"
            serial += 1
    notify("render", f"正在写入 {output}……")
    render_report(report, output)
    notify("done", f"报告已生成：{output}")

    return ProcessResult(
        demo=demo,
        report_path=output,
        validation=result,
        target_found=bool(report.get("meta", {}).get("target_found")),
        target_name=report.get("meta", {}).get("target_name"),
    )


def describe_failure(result: ValidationResult, max_differences: int = 12) -> str:
    """Readable multi-line message for a dialog box."""

    lines = [f"{result.title}", result.message]
    if result.fixture_path:
        lines.append(f"fixture: {result.fixture_path}")
    errors = list(result.errors)
    differences = result.differences
    if differences:
        lines.append("")
        lines.append("字段差异:")
        for check in differences[:max_differences]:
            lines.append(
                f"  {check.player} / {check.field}: 计分板={check.expected}，解析={check.actual}"
                + (f"（{check.detail}）" if check.detail else "")
            )
        if len(differences) > max_differences:
            lines.append(f"  ……另有 {len(differences) - max_differences} 项差异")
    if errors:
        lines.append("")
        lines.append("其他错误:")
        for error in errors[:max_differences]:
            lines.append(f"  - {error}")
        if len(errors) > max_differences:
            lines.append(f"  ……另有 {len(errors) - max_differences} 项")
    if result.status == "unverified":
        lines.append("")
        lines.append(
            "严格模式不会生成 report.html。请为这个 demo 准备计分板 fixture；"
            "说明见 README.txt，格式见 fixtures/FIXTURE_FORMAT.txt。"
        )
    elif result.status == "partial":
        lines.append("")
        lines.append("计分板 fixture 缺少必填字段，补齐后重新运行。")
    else:
        lines.append("")
        lines.append("未生成 report.html。请勿跳过校验；先修正差异。")
    return "\n".join(lines)


def find_default_output(demo_path: str | Path | None = None) -> Path:
    return app_output_dir()
