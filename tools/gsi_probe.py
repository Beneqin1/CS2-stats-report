"""Minimal CS2 GSI probe: save every payload and print a one-line summary.

Run this before touching the live statistics implementation.  It installs a
GSI config (optional), listens on a local port, and writes every POST body to
``gsi_dumps/`` together with ``manifest.jsonl`` and ``summary.json``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cs2stats.gsi_config import (  # noqa: E402
    DEFAULT_GSI_PORT,
    DEFAULT_TOKEN,
    build_config,
    find_cs2_cfg_dirs,
    install_config,
    write_local_config,
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    map_ = payload.get("map") or {}
    round_ = payload.get("round") or {}
    player = payload.get("player") or {}
    allplayers = payload.get("allplayers") or {}
    provider = payload.get("provider") or {}
    return {
        "received_at": _now(),
        "provider": {k: provider.get(k) for k in ("name", "appid", "version", "steamid", "timestamp")},
        "map": {
            k: map_.get(k)
            for k in ("name", "phase", "round", "num_matches_to_win_series", "current_spectators")
        },
        "team_scores": {
            "ct": (map_.get("team_ct") or {}).get("score"),
            "t": (map_.get("team_t") or {}).get("score"),
        },
        "round": {k: round_.get(k) for k in ("phase", "win_team", "bomb")},
        "player": {
            k: player.get(k)
            for k in ("steamid", "name", "team", "activity", "observer_slot")
        },
        "player_match_stats": (player.get("match_stats") or {}),
        "allplayers_count": len(allplayers),
        "top_level_keys": sorted(payload.keys()),
        "player_keys": sorted(player.keys()),
        "allplayers_keys": sorted(next(iter(allplayers.values())).keys()) if allplayers else [],
    }


class Probe:
    def __init__(self, out_dir: Path, port: int) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.port = port
        self.seq = 0
        self.lock = threading.Lock()
        self.payloads = 0
        self.bytes = 0
        self.phases: dict[tuple[str, str], int] = {}
        self.first = None
        self.last = None
        self.manifest_path = out_dir / "manifest.jsonl"
        self.manifest = self.manifest_path.open("w", encoding="utf-8", newline="\n")

    def handle(self, body: bytes) -> None:
        self.payloads += 1
        self.bytes += len(body)
        with self.lock:
            self.seq += 1
            seq = self.seq
        try:
            payload = json.loads(body.decode("utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            path = self.out_dir / f"payload_{seq:04d}_invalid.json"
            path.write_bytes(body)
            print(f"[{seq:04d}] invalid JSON: {exc}", flush=True)
            return
        summary = _summary(payload)
        map_ = payload.get("map") or {}
        round_ = payload.get("round") or {}
        phase = (str(map_.get("phase") or "?"), str(round_.get("phase") or "?"))
        self.phases[phase] = self.phases.get(phase, 0) + 1
        stamp = datetime.now().strftime("%H%M%S")
        safe_phase = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{phase[0]}_{phase[1]}")
        name = f"payload_{seq:04d}_{stamp}_{safe_phase}.json"
        path = self.out_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        summary["file"] = name
        summary["bytes"] = len(body)
        self.manifest.write(json.dumps(summary, ensure_ascii=False) + "\n")
        self.manifest.flush()
        if self.first is None:
            self.first = summary
        self.last = summary
        player = summary.get("player") or {}
        stats = summary.get("player_match_stats") or {}
        print(
            f"[{seq:04d}] map={map_.get('name')!r} phase={phase[0]}/{phase[1]} "
            f"round={map_.get('round')} score={summary['team_scores']} "
            f"players={summary['allplayers_count']} "
            f"local={player.get('name')!r} k/d/a={stats.get('kills')}/{stats.get('deaths')}/{stats.get('assists')}",
            flush=True,
        )

    def close(self) -> None:
        summary = {
            "generated_at": _now(),
            "port": self.port,
            "payloads": self.payloads,
            "bytes": self.bytes,
            "phase_counts": [
                {"map_phase": key[0], "round_phase": key[1], "count": value}
                for key, value in sorted(self.phases.items())
            ],
            "first": self.first,
            "last": self.last,
        }
        (self.out_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.manifest.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CS2 GSI payload probe")
    parser.add_argument("--port", type=int, default=DEFAULT_GSI_PORT)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    parser.add_argument("--out", type=Path, default=ROOT / "gsi_dumps")
    parser.add_argument("--cfg-dir", type=Path, help="CS2 game/csgo/cfg 目录；省略时自动检测")
    parser.add_argument("--install", action="store_true", help="把 GSI 配置写入 CS2 cfg 目录")
    parser.add_argument("--print-config", action="store_true", help="打印配置后退出")
    parser.add_argument("--duration", type=float, default=0, help="监听秒数；0 表示一直运行")
    args = parser.parse_args(argv)

    if args.print_config:
        print(build_config(args.port, args.token))
        return 0

    local = write_local_config(args.port, args.token)
    print(f"local config: {local}", flush=True)
    if args.install:
        try:
            installed = install_config(args.cfg_dir, args.port, args.token)
            print(f"installed config: {installed}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"install failed: {exc}", flush=True)
            for directory in find_cs2_cfg_dirs():
                print(f"detected cfg dir: {directory}", flush=True)
            return 2

    probe = Probe(args.out, args.port)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            try:
                probe.handle(body)
            except Exception as exc:  # noqa: BLE001
                print(f"probe error: {exc}", flush=True)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.daemon_threads = True
    print(f"listening on http://127.0.0.1:{args.port}  dumps -> {args.out}", flush=True)
    stop = threading.Event()

    def _stop(_signum: int, _frame: Any) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except Exception:
            pass

    thread = threading.Thread(target=server.serve_forever, name="gsi-probe", daemon=True)
    thread.start()
    started = time.monotonic()
    try:
        while not stop.is_set():
            if args.duration and time.monotonic() - started >= args.duration:
                break
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()
        probe.close()
        print(f"done: {probe.payloads} payloads, {probe.bytes} bytes", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
