"""Watch a folder for finished GOTV ``.dem`` files and render each one.

The local server created by the console ``map`` command writes its GOTV demo
when the match ends.  A demo is still open for writing while the map is
running, so we require the file size and mtime to stay unchanged for a few
seconds before handing it to :func:`cs2stats.app.process_demo`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .app import ProcessResult, describe_failure, process_demo
from .config import TARGET_DEFAULT
from .demo_parser import ParseError
from .validation import ValidationError, ValidationFailed

LOGGER = logging.getLogger(__name__)
Status = Callable[[str], None]
ReportCallback = Callable[[ProcessResult], None]
ErrorCallback = Callable[[str], None]

DEFAULT_STABLE_SECONDS = 3.0
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_LOOKBACK = 8 * 3600
DEFAULT_MAX_ATTEMPTS = 6
MIN_DEMO_BYTES = 16 * 1024


def default_output_dir() -> Path:
    from .paths import output_dir

    return output_dir()


def default_state_file() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or "."
    return Path(base) / "CS2StatsReport" / "watcher_state.json"


def _key(path: Path, stat: os.stat_result) -> str:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return f"{resolved}|{stat.st_size}|{stat.st_mtime_ns}"


@dataclass
class _Candidate:
    size: int
    mtime_ns: int
    since: float
    size_announced: int | None = None
    attempts: int = 0
    ready_at: float = 0.0


class DemoWatcher:
    """Single-folder, single-worker demo watcher.

    ``start()`` records the newest existing demo (and ignores older ones), then
    watches for new files.  Each file is processed once; a small JSON state
    file keeps that promise across program restarts.
    """

    def __init__(
        self,
        watch_dir: str | Path,
        *,
        out_dir: str | Path | None = None,
        target: str = TARGET_DEFAULT,
        fixture_path: str | Path | None = None,
        allow_missing_fixture: bool = True,
        stable_seconds: float = DEFAULT_STABLE_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        open_after: bool = True,
        state_file: str | Path | None = None,
        on_status: Status | None = None,
        on_report: ReportCallback | None = None,
        on_error: ErrorCallback | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.watch_dir = Path(watch_dir).expanduser()
        self.out_dir = Path(out_dir).expanduser() if out_dir else default_output_dir()
        self.target = target or TARGET_DEFAULT
        self.fixture_path = Path(fixture_path) if fixture_path else None
        self.allow_missing_fixture = bool(allow_missing_fixture)
        self.stable_seconds = max(1.0, float(stable_seconds))
        self.poll_seconds = max(0.2, float(poll_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.open_after = bool(open_after)
        self.state_file = Path(state_file).expanduser() if state_file else default_state_file()
        self.on_status = on_status or (lambda message: logger.info(message) if logger else None)
        self.on_report = on_report or (lambda result: None)
        self.on_error = on_error or (lambda message: None)
        self.logger = logger or LOGGER

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._candidates: dict[Path, _Candidate] = {}
        self._ignored: set[Path] = set()
        self._processed: set[str] = set()
        self._last_report: Path | None = None
        self._primed = False

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_report(self) -> Path | None:
        return self._last_report

    def _status(self, message: str) -> None:
        self.logger.info("[watch] %s", message)
        try:
            self.on_status(message)
        except Exception:
            self.logger.exception("status callback failed")

    def _error(self, message: str) -> None:
        self.logger.error("[watch] %s", message)
        try:
            self.on_error(message)
        except Exception:
            self.logger.exception("error callback failed")

    # ------------------------------------------------------------------
    def _list_demos(self) -> list[Path]:
        try:
            entries = list(self.watch_dir.iterdir())
        except OSError as exc:
            self._error(f"无法读取监听目录：{exc}")
            return []
        demos: list[Path] = []
        for entry in entries:
            try:
                if entry.is_file() and entry.suffix.lower() == ".dem":
                    demos.append(entry)
            except OSError:
                continue
        return demos

    def _stat(self, path: Path) -> os.stat_result | None:
        try:
            return path.stat()
        except OSError:
            return None

    def _load_state(self) -> None:
        if not self.state_file.is_file():
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                items = payload.get("processed", [])
            elif isinstance(payload, list):
                items = payload
            else:
                items = []
            self._processed = {str(item) for item in items if item}
        except (OSError, ValueError, TypeError):
            self.logger.warning("watcher state file unreadable; starting fresh", exc_info=True)

    def _save_state(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            items = list(self._processed)[-500:]
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"version": 1, "processed": items}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.state_file)
        except OSError:
            self.logger.warning("could not save watcher state", exc_info=True)

    def _is_processed(self, path: Path, stat: os.stat_result) -> bool:
        return _key(path, stat) in self._processed

    def _mark_processed(self, path: Path, stat: os.stat_result) -> None:
        with self._lock:
            self._processed.add(_key(path, stat))
            self._save_state()

    def _prime(self) -> None:
        """On startup process only the newest existing demo, never a backlog."""

        if self._primed:
            return
        self._primed = True
        demos = self._list_demos()
        if not demos:
            self._status(f"正在监听：{self.watch_dir}（等待新的 .dem）")
            return
        latest = max(demos, key=lambda p: (self._stat(p).st_mtime_ns if self._stat(p) else 0))
        self._ignored = {p for p in demos if p != latest}
        stat = self._stat(latest)
        if stat is None:
            return
        if self._is_processed(latest, stat):
            self._status(f"启动：最近的 demo 已处理过，跳过：{latest.name}")
            return
        self._candidates[latest] = _Candidate(stat.st_size, stat.st_mtime_ns, time.monotonic())
        self._status(
            f"启动：发现最新 demo {latest.name}；等待文件稳定 {self.stable_seconds:.0f} 秒后解析"
        )

    def _scan(self) -> None:
        now = time.monotonic()
        current = self._list_demos()
        current_set = set(current)
        with self._lock:
            for path in list(self._candidates):
                if path not in current_set:
                    self._candidates.pop(path, None)

        for path in current:
            if path in self._ignored:
                continue
            stat = self._stat(path)
            if stat is None:
                continue
            with self._lock:
                candidate = self._candidates.get(path)
                if candidate is None:
                    if self._is_processed(path, stat):
                        self._ignored.add(path)
                        continue
                    self._candidates[path] = _Candidate(stat.st_size, stat.st_mtime_ns, now)
                    self._status(f"发现新 demo：{path.name}；等待文件稳定…")
                    continue
                if candidate.size != stat.st_size or candidate.mtime_ns != stat.st_mtime_ns:
                    candidate.size = stat.st_size
                    candidate.mtime_ns = stat.st_mtime_ns
                    candidate.since = now
                    if candidate.size_announced != stat.st_size:
                        candidate.size_announced = stat.st_size
                        self._status(f"文件仍在写入：{path.name}（{stat.st_size / 1024 / 1024:.1f} MB）")
                    continue
                if now < candidate.ready_at or now - candidate.since < self.stable_seconds:
                    continue
                self._candidates.pop(path, None)

            if stat.st_size < MIN_DEMO_BYTES:
                # A .dem this small cannot hold a match; it is still being
                # created.  Do not parse it, do not error — retry later.
                outcome = "transient"
            else:
                outcome = self._process(path)
            if outcome in ("ok", "failed"):
                # "failed" is a hard, non-retryable failure (fixture mismatch)
                # that has already been reported; swallowing it here would
                # retry forever, so the file is done either way.
                self._mark_processed(path, stat)
            else:
                self._retry_later(path, candidate, stat)

    # ------------------------------------------------------------------
    def _retry_later(self, path: Path, candidate: _Candidate, stat: os.stat_result) -> None:
        """Requeue a demo that failed while it may still be written.

        A .dem is created (header written) when GOTV recording starts, long
        before the match ends.  A parse error at that stage means "not a
        complete demo yet", not "broken demo", so we back off and retry; only
        after ``max_attempts`` failures on an unchanged file do we report an
        error.  If the file keeps changing, the size/mtime key changes and it
        naturally becomes a fresh candidate again.
        """
        attempts = candidate.attempts + 1
        now = time.monotonic()
        if attempts > self.max_attempts:
            self._mark_processed(path, stat)
            self._error(
                f"{path.name} 重试 {self.max_attempts} 次后仍无法解析，已放弃该文件"
                "（可能是损坏或极不完整的录制）。"
            )
            return
        delay = min(5.0 * attempts, 30.0)
        fresh = self._stat(path)
        if fresh is None:
            return
        with self._lock:
            self._candidates[path] = _Candidate(
                fresh.st_size,
                fresh.st_mtime_ns,
                now,
                attempts=attempts,
                ready_at=now + delay,
            )
        self._status(
            f"{path.name} 暂时无法解析，可能仍在写入；{delay:.0f} 秒后重试"
            f"（第 {attempts}/{self.max_attempts} 次）"
        )

    def _process(self, path: Path) -> str:
        """Process one stable demo; returns "ok", "transient" or "failed".

        "transient" means the file is probably still being recorded and the
        caller should retry instead of alarming the user.  "failed" is a hard
        failure (fixture mismatch) that has already been reported and must not
        be retried.
        """
        self._status(f"文件已稳定，开始解析：{path.name}")

        def progress(stage: str, message: str) -> None:
            self._status(message)

        try:
            result = process_demo(
                path,
                out_dir=self.out_dir,
                target=self.target,
                fixture_path=self.fixture_path,
                allow_missing_fixture=self.allow_missing_fixture,
                progress=progress,
            )
        except ValidationFailed as exc:
            self._error(describe_failure(exc.result))
            return "failed"
        except (ParseError, ValidationError) as exc:
            self.logger.info("demo not parseable yet (will retry): %s", exc)
            return "transient"
        except Exception as exc:
            self.logger.exception("unexpected watcher error")
            return "transient"

        self._last_report = result.report_path
        try:
            self.on_report(result)
        except Exception:
            self.logger.exception("report callback failed")
        if self.open_after:
            try:
                webbrowser.open(result.report_path.resolve().as_uri())
            except Exception:
                self.logger.warning("could not open browser", exc_info=True)
        return "ok"

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        if not self.watch_dir.is_dir():
            raise FileNotFoundError(f"demo 监听目录不存在：{self.watch_dir}")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event.clear()
        self._load_state()
        self._prime()
        self._thread = threading.Thread(target=self._loop, name="demo-watcher", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        try:
            while not self._stop_event.is_set():
                self._scan()
                self._stop_event.wait(self.poll_seconds)
        finally:
            if self._stop_event.is_set():
                self._status("已停止监听 demo 文件夹。")

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        self._thread = None

    def wait(self) -> None:
        while self.running:
            time.sleep(0.5)

    # ------------------------------------------------------------------
    def process_latest(self, *, wait_stable: bool = True) -> ProcessResult | None:
        """Process the newest existing demo once (used by ``--watch-once``)."""

        if not self.watch_dir.is_dir():
            raise FileNotFoundError(f"demo 监听目录不存在：{self.watch_dir}")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()
        demos = self._list_demos()
        if not demos:
            self._status("目录中没有 .dem 文件。")
            return None
        latest = max(demos, key=lambda p: (self._stat(p).st_mtime_ns if self._stat(p) else 0))
        if wait_stable:
            self._status(f"等待 {latest.name} 文件稳定…")
            deadline = time.monotonic() + 60.0
            previous: tuple[int, int] | None = None
            stable_since = time.monotonic()
            while time.monotonic() < deadline:
                stat = self._stat(latest)
                if stat is None:
                    raise ParseError(f"demo 文件消失：{latest}")
                signature = (stat.st_size, stat.st_mtime_ns)
                if signature != previous:
                    previous = signature
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= self.stable_seconds:
                    break
                time.sleep(min(0.5, self.poll_seconds))
        stat = self._stat(latest)
        if stat is None:
            raise ParseError(f"demo 文件消失：{latest}")
        if self._is_processed(latest, stat):
            self._status(f"最近的 demo 已处理过，跳过：{latest.name}")
            return None
        if stat.st_size < MIN_DEMO_BYTES:
            self._status(f"{latest.name} 还在创建中，请等录制结束后再试。")
            return None
        outcome = self._process(latest)
        if outcome == "ok":
            self._mark_processed(latest, stat)
            return self._last_report
        if outcome == "failed":
            self._mark_processed(latest, stat)
            return None
        self._status(f"{latest.name} 暂时无法解析，可能仍在写入；未标记为已处理，稍后重试即可。")
        return None
