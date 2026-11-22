"""Render the self-contained HTML report."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .demo_parser import ParseError


def _resource_root() -> Path:
    """Where templates/static live in dev and in a PyInstaller bundle."""

    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
        candidates.extend([base / "cs2stats", base])
        candidates.append(Path(sys.executable).resolve().parent / "cs2stats")
    here = Path(__file__).resolve().parent
    candidates.extend([here, here.parent])
    for candidate in candidates:
        if (candidate / "templates" / "report.html.j2").exists():
            return candidate
    raise ParseError("找不到报告模板 templates/report.html.j2，安装包可能不完整。")


def render_report(report: dict[str, Any], output_path: Path) -> Path:
    root = _resource_root()
    template_dir = root / "templates"
    static_dir = root / "static"
    echarts_path = static_dir / "echarts.min.js"
    if not echarts_path.exists():
        raise ParseError("找不到 static/echarts.min.js，安装包可能不完整。")

    env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("report.html.j2")

    # Escape "<" so a player name containing "</script>" cannot break the
    # embedded JSON block.  The resulting text is still valid JSON.
    data_json = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c")
    echarts_js = echarts_path.read_text(encoding="utf-8")
    if "</script" in echarts_js:
        echarts_js = echarts_js.replace("</script", "<\\/script")

    html = template.render(report=report, data_json=data_json, echarts_js=echarts_js)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path
