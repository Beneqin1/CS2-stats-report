"""Watch a folder for finished GOTV ``.dem`` files and render each one.

The local server created by the console ``map`` command writes its GOTV demo
when the match ends.  A demo is still open for writing while the map is
running, and CS2 writes in blocks with multi-second pauses, so a file is only
handed to :func:`cs2stats.app.process_demo` after its size and mtime stayed
identical across several samples taken seconds apart.  Parse failures are
classified: EOF/truncation-style errors mean "still recording" and go to a
backoff retry queue, anything else is reported immediately as a hard error.
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
from .logging_setup import log_file
from .validation import ValidationError, ValidationFailed

LOGGER = logging.getLogger(__name__)
Status = Callable[[str], None]
ReportCallback = Callable[[ProcessResult], None]
ErrorCallback = Callable[[str], None]

DEFAULT_STABLE_SECONDS = 3.0
DEFAULT_SAMPLE_INTERVAL = 3.0
DEFAULT_STABLE_SAMPLES = 3
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_MAX_ATTEMPTS = 5
MIN_DEMO_BYTES = 256 * 1024
BACKOFF_SECONDS = (5.0, 15.0, 30.0, 30.0, 30.0)

# Lower-cased substrings that mark a parse error as "file still being
# recorded" (retryable).  Empirically captured from demoparser2 / our own
# parser on truncated demos:
#   "parse error: file-info header offset ... outside command stream 16..N"
# A garbage file instead yields "invalid demo file: magic bytes mismatch"
# and mid-file corruption yields "snappy: corrupt input" — both hard errors.
_TRANSIENT_HINTS = (
    "outside command stream",
    "unexpected eof",
    "failed to fill whole buffer",
    "incomplete",
    "truncat",
    "permission",
    "无法读取 demo 文件",
    "demo.stats 为空",
)


def is_transient_parse_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a demo that is still being recorded.

    Only EOF / truncation / file-lock style errors qualify.  Anything else
    (bad magic bytes, decompression errors, structural problems) is a
    genuinely broken file and must surface as an error instead of retrying.
    """

    message = str(exc).lower()
    return any(hint in message for hint in _TRANSIENT_HINTS)


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
    last_sample_at: float = 0.0
    stable_samples: int = 0
    tiny_noted: bool = False


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
        sample_interval: float = DEFAULT_SAMPLE_INTERVAL,
        stable_samples: int = DEFAULT_STABLE_SAMPLES,
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
        self.stable_seconds = max(0.0, float(stable_seconds))
        self.sample_interval = max(1.0, float(sample_interval))
        self.stable_samples = max(1, int(stable_samples))
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
        self._candidates[latest] = _Candidate(
            stat.st_size, stat.st_mtime_ns, time.monotonic(), last_sample_at=time.monotonic()
        )
        self._status(
            f"启动：发现最新 demo {latest.name}；录制结束（连续 {self.stable_samples} 次采样不变）后自动解析"
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
                    self._candidates[path] = _Candidate(
                        stat.st_size, stat.st_mtime_ns, now, last_sample_at=now
                    )
                    self._boost_older_candidates(newcomer=path, now=now)
                    self._status(
                        f"发现新 demo：{path.name}；录制结束后自动解析"
                        f"（连续 {self.stable_samples} 次采样不变，间隔 ≥{self.sample_interval:.0f} 秒）"
                    )
                    continue
                if now < candidate.ready_at:
                    continue
                if not self._sample_stability(path, candidate, stat, now):
                    continue
                if stat.st_size < MIN_DEMO_BYTES:
                    # 太小：只有头部的文件必然还在录制（完整 demo 至少数 MB）。
                    # 不解析、不烧重试次数，持续等待它长大或被删除。
                    self._handle_tiny(path, candidate, stat, now)
                    continue
                self._candidates.pop(path, None)

            self.logger.info(
                "[watch] 就绪判定通过 %s：连续 %d 次采样（间隔 %.0f 秒）大小/mtime 未变，%.1f MB",
                path.name, self.stable_samples, self.sample_interval, stat.st_size / 1048576,
            )
            outcome = self._process(path)
            if outcome in ("ok", "failed", "hard"):
                # "failed"/"hard" 都是已报告给用户、不应再碰该文件的硬失败；
                # 吞掉它们会导致无限重试，所以无论成功失败都标记已处理。
                self._mark_processed(path, stat)
            else:
                self._retry_later(path, candidate, stat)

    # ------------------------------------------------------------------
    def _sample_stability(
        self, path: Path, candidate: _Candidate, stat: os.stat_result, now: float
    ) -> bool:
        """Take one stability sample; True only after enough identical samples.

        Samples are at least ``sample_interval`` seconds apart and the file is
        ready only after ``stable_samples`` consecutive identical samples
        (size + mtime) plus at least ``stable_seconds`` since the last change.
        A single matching poll never readies a file — CS2 writes in blocks and
        can pause for a couple of seconds mid-recording.  Caller holds the lock.
        """

        if candidate.size != stat.st_size or candidate.mtime_ns != stat.st_mtime_ns:
            candidate.size = stat.st_size
            candidate.mtime_ns = stat.st_mtime_ns
            candidate.stable_samples = 0
            candidate.last_sample_at = now
            candidate.since = now
            candidate.tiny_noted = False
            if candidate.size_announced != stat.st_size:
                candidate.size_announced = stat.st_size
                self._status(f"文件仍在写入：{path.name}（{stat.st_size / 1024 / 1024:.1f} MB）")
            self.logger.info(
                "[watch] 采样 %s：大小/mtime 变化（%.1f MB），稳定计数归零",
                path.name, stat.st_size / 1048576,
            )
            return False
        if now - candidate.last_sample_at < self.sample_interval:
            return False
        candidate.last_sample_at = now
        candidate.stable_samples += 1
        self.logger.info(
            "[watch] 采样 %s：%.1f MB 未变，连续稳定 %d/%d",
            path.name, stat.st_size / 1048576, candidate.stable_samples, self.stable_samples,
        )
        return (
            candidate.stable_samples >= self.stable_samples
            and (now - candidate.since) >= self.stable_seconds
        )

    def _boost_older_candidates(self, *, newcomer: Path, now: float) -> None:
        """Fast-ready older candidates when a newer demo appears.

        CS2 records only one demo per server: a newer ``.dem`` in the same
        folder means the previous recording has definitely ended, so waiting
        for more stability samples is pointless.  Caller holds the lock.
        """

        for path, candidate in self._candidates.items():
            if path is newcomer:
                continue
            if candidate.stable_samples >= self.stable_samples and candidate.ready_at <= now:
                continue
            candidate.stable_samples = self.stable_samples
            candidate.ready_at = now
            self._status(f"检测到更新的 demo（{newcomer.name}），判定 {path.name} 录制已结束")
            self.logger.info(
                "[watch] 辅助就绪信号：%s 因更新的文件 %s 出现而直接判定就绪",
                path.name, newcomer.name,
            )

    def _handle_tiny(self, path: Path, candidate: _Candidate, stat: os.stat_result, now: float) -> None:
        """A stable but tiny .dem is a just-created (or aborted) recording.

        Never parse it and never burn retry attempts; if it sits around for a
        while, say so once instead of staying silent.  Caller holds the lock.
        """

        if not candidate.tiny_noted and now - candidate.since >= 60.0:
            candidate.tiny_noted = True
            self._status(
                f"{path.name} 只有 {stat.st_size / 1024:.0f} KB 且长时间未增长，"
                "可能是已中止的录制；继续等待，不会解析该文件。"
            )
            self.logger.warning(
                "[watch] %s 体积过小（%d 字节）且 60 秒未增长，疑似中止的录制，继续观察",
                path.name, stat.st_size,
            )

    # ------------------------------------------------------------------
    def _retry_later(self, path: Path, candidate: _Candidate, stat: os.stat_result) -> None:
        """Requeue a demo whose parse failed with an EOF/truncation error.

        A .dem is created (header written) when GOTV recording starts, long
        before the match ends.  An EOF-class parse error at that stage means
        "not a complete demo yet", not "broken demo", so we back off
        exponentially (5s -> 15s -> 30s, capped) and retry; only after
        ``max_attempts`` failures do we report an explicit error.  If the file
        keeps changing, the size/mtime signature changes and the stability
        sampling naturally restarts.
        """
        attempts = candidate.attempts + 1
        now = time.monotonic()
        if attempts > self.max_attempts:
            self._mark_processed(path, stat)
            self._error(
                f"{path.name} 重试 {self.max_attempts} 次后仍无法解析，已放弃该文件"
                f"（可能是损坏或极不完整的录制；详情见日志 {log_file()}）。"
            )
            return
        delay = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]
        fresh = self._stat(path)
        if fresh is None:
            return
        with self._lock:
            # stable_samples is pre-filled: the file already passed the
            # stability gate once, so after the backoff only one confirming
            # sample is needed (a changed signature still resets the count).
            self._candidates[path] = _Candidate(
                fresh.st_size,
                fresh.st_mtime_ns,
                now,
                attempts=attempts,
                ready_at=now + delay,
                last_sample_at=now,
                stable_samples=self.stable_samples,
            )
        self._status(
            f"{path.name} 暂时无法解析，可能仍在写入；{delay:.0f} 秒后重试"
            f"（第 {attempts}/{self.max_attempts} 次）"
        )
        self.logger.info(
            "[watch] %s 进入重试队列：第 %d/%d 次，%.0f 秒后重试",
            path.name, attempts, self.max_attempts, delay,
        )

    def _process(self, path: Path) -> str:
        """Process one stable demo; returns "ok", "transient", "hard" or "failed".

        "transient" means an EOF/truncation-class error — the file is probably
        still being recorded and the caller should retry instead of alarming
        the user.  "hard" is a genuine corruption or unexpected error: report
        it immediately and never retry.  "failed" is a fixture mismatch that
        has already been reported and must not be retried.
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
        except ValidationError as exc:
            self._error(f"{path.name} 的校验配置有问题（不重试）：{exc}")
            return "hard"
        except ParseError as exc:
            if is_transient_parse_error(exc):
                self.logger.info("[watch] %s 判定为未录完（可重试）：%s", path.name, exc)
                return "transient"
            self._error(f"{path.name} 解析失败（文件损坏类错误，不重试）：{exc}")
            return "hard"
        except Exception:
            self.logger.exception("[watch] %s 解析出现意外错误", path.name)
            self._error(f"{path.name} 解析出现意外错误（不重试），详情见日志：{log_file()}")
            return "hard"

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
            self._status(
                f"等待 {latest.name} 录制结束（连续 {self.stable_samples} 次采样不变）…"
            )
            deadline = time.monotonic() + 90.0
            candidate = _Candidate(0, 0, time.monotonic())
            while time.monotonic() < deadline:
                stat = self._stat(latest)
                if stat is None:
                    raise ParseError(f"demo 文件消失：{latest}")
                if self._sample_stability(latest, candidate, stat, time.monotonic()):
                    break
                time.sleep(min(1.0, self.poll_seconds))
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
