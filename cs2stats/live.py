"""Live GSI session controller: match end -> report -> optional demo enrichment.

The HTTP listener lives in :mod:`cs2stats.gsi`; this module owns the lifecycle
around it.  It deliberately keeps the report generation out of the HTTP thread:
CS2 has a five second timeout, and rendering the 1 MB self-contained HTML can
take longer than that.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from .config import TARGET_DEFAULT
from .demo_parser import ParsedDemo, ParseError, load_demo
from .gsi import GSIListener, LiveMatch, build_live_report, live_validation
from .gsi_config import (
    DEFAULT_GSI_PORT,
    DEFAULT_TOKEN,
    find_cs2_cfg_dirs,
    install_config,
    local_gsi_dir,
    write_local_config,
)
from .render import render_report
from .validation import ValidationError, ValidationResult, validate_demo

LOGGER = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
ReportCallback = Callable[["LiveReportResult"], None]
ErrorCallback = Callable[[str], None]


@dataclass
class LiveReportResult:
    report_path: Path
    match: LiveMatch
    report: dict
    validation: dict
    target_found: bool
    target_name: str | None
    demo: ParsedDemo | None = None
    demo_validated: bool = False
    incomplete: list[str] | None = None

    @property
    def status(self) -> str:
        return str(self.validation.get("status", "live"))

    @property
    def enriched(self) -> bool:
        return self.demo is not None


def default_output_dir() -> Path:
    """Report folder beside the executable (see :mod:`cs2stats.paths`)."""

    from .paths import output_dir

    return output_dir()


def _iter_demos(root: Path, max_files: int = 2000) -> Iterable[Path]:
    if not root.is_dir():
        return
    count = 0
    for current, dirs, files in os.walk(root):
        depth = len(Path(current).relative_to(root).parts)
        if depth >= 3:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.lower().endswith(".dem"):
                yield Path(current) / name
                count += 1
                if count >= max_files:
                    return


def default_demo_dirs() -> list[Path]:
    """Likely demo-recording locations, without scanning the whole disk."""

    result: list[Path] = []
    documents = Path.home() / "Documents"
    for path in (
        documents / "My Games" / "Counter-Strike Global Offensive",
        documents / "CS2Stats" / "demos",
        Path.home() / "Videos",
    ):
        if path.is_dir():
            result.append(path)
    try:
        from .gsi_config import steam_libraries

        for library in steam_libraries():
            game = library / "steamapps" / "common" / "Counter-Strike Global Offensive" / "game" / "csgo"
            if game.is_dir():
                result.append(game)
    except Exception:
        LOGGER.debug("could not enumerate Steam libraries", exc_info=True)
    # Keep order, drop duplicates.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in result:
        marker = str(path).casefold()
        if marker not in seen:
            seen.add(marker)
            unique.append(path)
    return unique


def find_recent_demo(
    since: float | None = None,
    demo_dirs: Iterable[str | Path] | None = None,
    *,
    lookback_seconds: float = 8 * 3600,
) -> Path | None:
    """Find the newest ``.dem`` modified during (or just after) this match.

    This is intentionally a best-effort search of known recording folders.  A
    missing demo is normal and must never be treated as an error.
    """

    started = float(since) if since is not None else (time.time() - lookback_seconds)
    threshold = started - 300.0  # allow a short pre-match recording
    roots = [Path(p) for p in demo_dirs] if demo_dirs is not None else default_demo_dirs()
    candidates: list[tuple[float, Path]] = []
    for root in roots:
        try:
            for path in _iter_demos(root):
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    continue
                if mtime >= threshold:
                    candidates.append((mtime, path))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def apply_live_meta(
    report: dict,
    match: LiveMatch,
    *,
    demo: ParsedDemo | None = None,
    demo_validation: ValidationResult | None = None,
) -> dict:
    """Attach source/enrichment information used by the Jinja template."""

    meta = report.setdefault("meta", {})
    source = "spectator" if match.source == "spectator" else "local"
    meta.update(
        {
            "mode": "live",
            "source": f"{source}+demo" if demo is not None else source,
            "incomplete": list(dict.fromkeys(match.incomplete)),
            "live_incomplete": list(dict.fromkeys(match.incomplete)),
            "live_started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "spectator": match.source == "spectator",
            "enriched_with_demo": demo is not None,
            "player_count": len(report.get("players", [])),
        }
    )
    if demo is None:
        meta["demo_file"] = (
            "GSI 实时监听 · spectator（10 人）"
            if match.source == "spectator"
            else "GSI 实时监听 · 本地玩家"
        )
        meta["demo_sha256"] = "live-gsi"
    else:
        meta["demo_file"] = f"GSI 实时监听 + demo 补全：{demo.path.name}"
        meta["demo_sha256"] = demo.sha256
        meta["demo_map"] = demo.map_name
        meta["demo_rounds"] = len(demo.counted_rounds)
        if demo_validation is not None:
            meta["fixture_verified"] = demo_validation.status == "verified"
    return report


def _demo_matches_live(match: LiveMatch, demo: ParsedDemo) -> tuple[bool, str]:
    """Conservative identity check before a recent demo may replace GSI data.

    A demo is only useful for this match when map, counted rounds and the
    final score agree.  Anything else is likely last game's recording, and
    silently using it would put another match's K/D/A into this report.
    """

    if not match.rounds:
        return False, "GSI 没有完整回合，无法与 demo 对齐"
    live_map = (match.map_name or "").lower().removeprefix("de_")
    demo_map = (demo.map_name or "").lower().removeprefix("de_")
    if live_map and demo_map and live_map != demo_map:
        return False, f"地图不一致（GSI {live_map} / demo {demo_map}）"
    live_rounds = len(match.rounds)
    demo_rounds = len(demo.counted_rounds)
    if live_rounds != demo_rounds:
        return False, f"回合数不一致（GSI {live_rounds} / demo {demo_rounds}）"
    live_score = sorted(int(v) for v in match.scores.values())
    demo_score = sorted(int(team.score) for team in demo.teams.values())
    if live_score != demo_score:
        return False, f"比分不一致（GSI {live_score[0]}-{live_score[-1]} / demo {demo_score[0]}-{demo_score[-1]}）"
    return True, "地图、回合数、比分一致"


def _demo_live_validation(
    match: LiveMatch,
    demo: ParsedDemo,
    demo_validation: ValidationResult | None,
) -> dict:
    """Validation payload for a demo-enriched live report."""

    base = live_validation(match)
    source = "spectator（10 人）" if match.source == "spectator" else "本地玩家"
    if demo_validation is not None and demo_validation.status == "verified":
        return {
            "status": "verified",
            "title": "GSI 实时监听 + demo 补全（计分板 fixture 校验通过）",
            "message": (
                f"已自动定位并解析 {demo.path.name}，精确层 K/D/A、ADR、KAST、Open Kills "
                "来自该 demo 并与计分板 fixture 逐项一致。"
                "GSI 仅负责触发与实时来源；本页面的精确字段以 demo 为准。"
            ),
            "source": "spectator+demo" if match.source == "spectator" else "local+demo",
            "incomplete": list(dict.fromkeys(match.incomplete)),
            "fixture": str(demo_validation.fixture_path or ""),
            "checks": [],
        }
    return {
        "status": "live",
        "title": "GSI 实时监听 + demo 补全（demo 未与计分板 fixture 校验）",
        "message": (
            f"已自动定位并解析 {demo.path.name}，K/D/A、ADR、KAST、Open Kills 使用 demo 解析值；"
            "没有找到与该 demo 匹配的计分板 fixture，因此不能宣称已与游戏内计分板逐项一致。"
            "GSI 来源：" + source + "。"
            + (f"数据缺口：{'；'.join(base['incomplete'])}" if base.get("incomplete") else "")
        ),
        "source": "spectator+demo" if match.source == "spectator" else "local+demo",
        "incomplete": list(dict.fromkeys(match.incomplete)),
        "fixture": None,
        "checks": [],
    }


def finish_match(
    match: LiveMatch,
    *,
    target: str = TARGET_DEFAULT,
    out_path: str | Path | None = None,
    out_dir: str | Path | None = None,
    demo_dirs: Iterable[str | Path] | None = None,
    demo_path: str | Path | None = None,
    fixture_path: str | Path | None = None,
    open_after: bool = True,
    progress: Callable[[str, str], None] | None = None,
) -> LiveReportResult:
    """Build one live report from a finished match.

    ``demo_path`` wins over the automatic search.  If a demo is supplied or
    found, it is parsed and (when a fixture matches) validated; otherwise the
    report stays GSI-only and loudly says so.
    """

    notify = progress or (lambda stage, message: LOGGER.info("[%s] %s", stage, message))
    incomplete = list(dict.fromkeys(match.incomplete))

    demo: ParsedDemo | None = None
    demo_validation: ValidationResult | None = None
    candidate = Path(demo_path) if demo_path else None
    if candidate is None:
        candidate = find_recent_demo(demo_dirs=demo_dirs)
    if candidate is not None:
        try:
            notify("enrich", f"正在尝试用 demo 补全：{candidate.name}")
            demo = load_demo(candidate)
            matches, reason = _demo_matches_live(match, demo)
            if not matches:
                note = f"最近 demo 与 GSI 对局不一致（{reason}），未用于补全"
                if note not in match.incomplete:
                    match.incomplete.append(note)
                notify("enrich", note)
                demo = None
                candidate = None
            if demo is not None:
                try:
                    demo_validation = validate_demo(demo, fixture_path)
                except (ValidationError, OSError) as exc:
                    notify("enrich", f"demo fixture 校验出错，继续使用 demo 解析值：{exc}")
                    demo_validation = None
        except (ParseError, OSError) as exc:
            notify("enrich", f"demo 补全失败，保留 GSI 数据：{exc}")
            demo = None

    if demo is not None:
        validation = _demo_live_validation(match, demo, demo_validation)
        try:
            report = require_build_report(demo, target, validation)
        except Exception:
            LOGGER.exception("demo-enriched report failed; falling back to GSI-only")
            notify("enrich", "demo 补全报告构建失败，回退到 GSI 数据。")
            demo = None
            demo_validation = None
            validation = live_validation(match)
            report = build_live_report(match, target)
            apply_live_meta(report, match)
            demo_validated = False
        else:
            apply_live_meta(report, match, demo=demo, demo_validation=demo_validation)
            demo_validated = bool(demo_validation and demo_validation.status == "verified")
    else:
        validation = live_validation(match)
        report = build_live_report(match, target)
        apply_live_meta(report, match)
        demo_validated = False

    if out_path is not None:
        output = Path(out_path)
        output.parent.mkdir(parents=True, exist_ok=True)
    else:
        directory = Path(out_dir) if out_dir else default_output_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        meta = report.get("meta", {})
        map_part = (meta.get("map") or meta.get("map_name") or "match").replace(" ", "_")
        output = directory / f"report_{map_part}_{stamp}.html"
    notify("render", f"正在写入报告：{output}")
    render_report(report, output)

    result = LiveReportResult(
        report_path=output,
        match=match,
        report=report,
        validation=validation,
        target_found=bool(report.get("meta", {}).get("target_found")),
        target_name=report.get("meta", {}).get("target_name"),
        demo=demo,
        demo_validated=demo_validated,
        incomplete=list(dict.fromkeys(match.incomplete)),
    )
    notify("done", f"报告已生成：{output}")
    if open_after:
        try:
            webbrowser.open(output.resolve().as_uri())
        except Exception:
            LOGGER.warning("could not open report in browser", exc_info=True)
    return result


def require_build_report(demo: ParsedDemo, target: str, validation: dict) -> dict:
    """Small indirection that makes the import explicit for tests/packaging."""

    from .report_data import build_report as _build

    return _build(demo, target_name=target, validation=validation)


class LiveSession:
    """Start a GSI listener, wait for gameover, then emit one report."""

    def __init__(
        self,
        *,
        target: str = TARGET_DEFAULT,
        out_dir: str | Path | None = None,
        demo_dirs: Iterable[str | Path] | None = None,
        fixture_path: str | Path | None = None,
        port: int = DEFAULT_GSI_PORT,
        token: str = DEFAULT_TOKEN,
        open_after: bool = True,
        on_status: StatusCallback | None = None,
        on_report: ReportCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.target = target
        self.out_dir = Path(out_dir) if out_dir else default_output_dir()
        self.demo_dirs = list(demo_dirs) if demo_dirs is not None else None
        self.fixture_path = fixture_path
        self.port = port
        self.token = token
        self.open_after = open_after
        self.on_status = on_status or (lambda message: None)
        self.on_report = on_report or (lambda result: None)
        self.on_error = on_error or (lambda message: None)
        self.listener: GSIListener | None = None
        self.started_at: float | None = None
        self._finishing = False
        self._stopped = False
        self.last_report: LiveReportResult | None = None

    @property
    def running(self) -> bool:
        return self.listener is not None and self.listener.running

    def install_config(self) -> Path:
        """Install the cfg and keep a local reference copy."""

        write_local_config(self.port, self.token)
        dirs = find_cs2_cfg_dirs()
        target = install_config(dirs[0] if dirs else None, self.port, self.token)
        return target

    def start(self) -> None:
        if self.running:
            return
        cfg = self.install_config()
        LOGGER.info("GSI config installed: %s", cfg)
        self.listener = GSIListener(port=self.port, token=self.token, on_events=self._on_events)
        self.listener.start()
        self.started_at = time.time()
        self.on_status(f"监听中：127.0.0.1:{self.port}（CS2 已读取配置后才会推送）")

    def _on_events(self, events: list[str]) -> None:
        if "match_end" not in events or self._finishing:
            return
        self._finishing = True
        # The HTTP handler must return quickly; build the report off-thread.
        threading.Thread(target=self._finish, daemon=True, name="gsi-finish").start()

    def _finish(self) -> None:
        listener = self.listener
        if listener is None:
            return
        match = listener.match
        self.on_status("比赛结束，正在生成报告……")
        try:
            result = finish_match(
                match,
                target=self.target,
                out_dir=self.out_dir,
                demo_dirs=self.demo_dirs,
                fixture_path=self.fixture_path,
                open_after=self.open_after,
                progress=lambda stage, message: self.on_status(message),
            )
            self.last_report = result
            self.on_report(result)
        except Exception as exc:  # noqa: BLE001 - surfaced to the GUI
            LOGGER.exception("live report generation failed")
            self.on_error(f"生成实时报告失败：{exc}")
        finally:
            self.stop()
            self.on_status("已停止监听。可点击“开始监听”等待下一场。")

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        listener = self.listener
        self.listener = None
        if listener is not None:
            listener.stop()

    def wait(self, timeout: float | None = None) -> LiveReportResult | None:
        """Headless helper used by the CLI; returns the report or None."""

        deadline = None if timeout is None else time.time() + timeout
        while self.running:
            if deadline is not None and time.time() >= deadline:
                break
            time.sleep(0.2)
        return self.last_report

    @property
    def local_config_path(self) -> Path:
        return local_gsi_dir() / "gamestate_integration_cs2stats.cfg"
