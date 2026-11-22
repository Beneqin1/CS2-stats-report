"""Validate the exact layer against a scoreboard fixture.

The five fields below are the only ones this tool claims to reproduce exactly.
They must match the in-game scoreboard (or a manual transcription of it) for
every player.  If any check fails, the caller must not render a final report.

``opening_kills`` is deliberately not in this tuple.  It remains a labelled
reference field: GSI and the report both expose it, but it is not part of the
scoreboard-gated exact layer.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable

from .demo_parser import ParsedDemo, Player

EXACT_FIELDS = ("kills", "deaths", "assists", "adr", "kast")
REFERENCE_FIELDS = ("opening_kills",)
FIELD_LABELS = {
    "kills": "Kills",
    "deaths": "Deaths",
    "assists": "Assists",
    "adr": "ADR",
    "kast": "KAST",
    "opening_kills": "Open Kills",
}
INT_FIELDS = {"kills", "deaths", "assists", "opening_kills"}
PERCENT_FIELDS = {"kast"}
DEFAULT_PRECISION = {"adr": 1, "kast": 1}


class ValidationError(RuntimeError):
    """The fixture itself could not be read or interpreted."""


class ValidationFailed(RuntimeError):
    """The demo disagrees with the fixture; no final HTML may be produced."""

    def __init__(self, result: "ValidationResult"):
        self.result = result
        super().__init__(result.message)


@dataclass
class Check:
    player: str
    field: str
    expected: Any
    actual: Any
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "player": self.player,
            "field": self.field,
            "label": FIELD_LABELS.get(self.field, self.field),
            "expected": self.expected,
            "actual": self.actual,
            "ok": self.ok,
            "detail": self.detail,
        }


@dataclass
class ValidationResult:
    status: str
    title: str
    message: str
    fixture_path: Path | None = None
    fixture: dict[str, Any] = field(default_factory=dict)
    demo_sha256: str = ""
    checks: list[Check] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    matched_players: int = 0
    total_players: int = 0

    @property
    def ok(self) -> bool:
        return self.status == "verified"

    @property
    def differences(self) -> list[Check]:
        return [check for check in self.checks if not check.ok]

    def report_payload(self) -> dict[str, Any]:
        fixture_name = self.fixture_path.name if self.fixture_path else None
        return {
            "status": self.status,
            "title": self.title,
            "message": self.message,
            "fixture": str(self.fixture_path) if self.fixture_path else None,
            "fixture_name": fixture_name,
            "source": self.fixture.get("source") if isinstance(self.fixture, dict) else None,
            "fixture_precision": (
                self.fixture.get("precision") if isinstance(self.fixture, dict) else None
            ),
            "adr_rounding": (
                self.fixture.get("adr_rounding") if isinstance(self.fixture, dict) else None
            ),
            "validated_fields": (
                self.fixture.get("validated_fields") if isinstance(self.fixture, dict) else None
            ),
            "demo_sha256": self.demo_sha256,
            "matched_players": self.matched_players,
            "total_players": self.total_players,
            "check_count": len(self.checks),
            "difference_count": len(self.differences),
            "checks": [check.as_dict() for check in self.checks],
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _normal_key(value: str) -> str:
    import unicodedata

    text = unicodedata.normalize("NFKC", str(value or ""))
    return " ".join(text.casefold().split())


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return number


def _round_value(value: float, decimals: int, rounding=ROUND_HALF_UP) -> Decimal:
    quant = Decimal(1).scaleb(-int(decimals))
    return _to_decimal(value).quantize(quant, rounding=rounding)


def _round_half_up(value: float, decimals: int) -> Decimal:
    return _round_value(value, decimals, ROUND_HALF_UP)


def _precision_for(fixture: dict[str, Any], field_name: str) -> int:
    precision = fixture.get("precision") or {}
    if isinstance(precision, dict) and field_name in precision:
        try:
            return max(0, min(6, int(precision[field_name])))
        except (TypeError, ValueError):
            pass
    for key in (f"{field_name}_decimals", f"{field_name}_precision"):
        if key in fixture:
            try:
                return max(0, min(6, int(fixture[key])))
            except (TypeError, ValueError):
                pass
    return DEFAULT_PRECISION.get(field_name, 2)


def _expected_value(entry: dict[str, Any], field_name: str) -> Any:
    if field_name in entry:
        return entry[field_name]
    if field_name == "opening_kills":
        return entry.get("open_kills", entry.get("opening_kill"))
    return None


def _format_actual(field_name: str, actual: Any) -> str:
    if field_name in INT_FIELDS:
        return str(int(actual))
    return f"{float(actual):.4f}".rstrip("0").rstrip(".")


def _load_fixture_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValidationError(f"无法读取 fixture：{path}（{exc}）") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"fixture 不是合法 JSON：{path}（第 {exc.lineno} 行）") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"fixture 顶层必须是对象：{path}")
    return data


def _fixture_entries(fixture: dict[str, Any]) -> list[dict[str, Any]]:
    players = fixture.get("players")
    if isinstance(players, list):
        return [entry for entry in players if isinstance(entry, dict)]
    if isinstance(players, dict):
        entries: list[dict[str, Any]] = []
        for key, value in players.items():
            if not isinstance(value, dict):
                raise ValidationError(f"players.{key} 必须是对象")
            entry = dict(value)
            entry.setdefault("name", key)
            entries.append(entry)
        return entries
    raise ValidationError("fixture 缺少 players（对象或数组）")


def _fixture_matches_demo(fixture: dict[str, Any], demo: ParsedDemo) -> bool:
    meta = fixture.get("demo") or {}
    if not isinstance(meta, dict):
        return False
    expected_sha = str(meta.get("sha256") or "").strip().lower()
    if expected_sha and expected_sha != demo.sha256.lower():
        return False
    expected_name = str(meta.get("filename") or "").strip()
    if expected_name and expected_name.casefold() != demo.path.name.casefold():
        # The hash is authoritative when present; the filename is only a
        # fallback for fixtures that do not record a hash.
        return bool(expected_sha)
    return True


def find_fixture(demo: ParsedDemo, explicit: str | Path | None = None) -> Path | None:
    """Find a fixture for *demo*.

    Search order: explicit path, sidecar next to the demo, ``fixtures/`` next
    to the demo, the project ``fixtures/`` directory, then a bundle directory.
    A fixture matches when ``demo.sha256`` equals the demo hash, or (when no
    hash is present) the filename matches.
    """

    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path
        raise ValidationError(f"指定的 fixture 不存在：{path}")

    candidates: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(path)

    demo_path = Path(demo.path)
    add(demo_path.with_suffix(".fixture.json"))
    add(demo_path.with_name(demo_path.stem + ".fixture.json"))
    add(demo_path.parent / "fixtures" / (demo_path.stem + ".fixture.json"))
    add(demo_path.parent / "fixtures" / (demo.sha256 + ".json"))

    here = Path(__file__).resolve().parent
    roots = [here.parent, here.parent.parent]
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
        if hasattr(sys, "_MEIPASS"):
            roots.append(Path(sys._MEIPASS))
    for root in roots:
        fixtures_dir = root / "fixtures"
        add(fixtures_dir / (demo_path.stem + ".fixture.json"))
        add(fixtures_dir / (demo.sha256 + ".json"))
        if fixtures_dir.is_dir():
            for path in sorted(fixtures_dir.glob("*.json")):
                add(path)

    for path in candidates:
        if not path.is_file():
            continue
        try:
            fixture = _load_fixture_file(path)
        except ValidationError:
            continue
        if _fixture_matches_demo(fixture, demo):
            return path
    return None


def _resolve_players(
    demo: ParsedDemo, entries: list[dict[str, Any]]
) -> tuple[dict[str, Player], list[str], list[str]]:
    players = list(demo.players)
    by_name: dict[str, list[Player]] = {}
    by_steamid: dict[str, Player] = {}
    for player in players:
        by_name.setdefault(_normal_key(player.name), []).append(player)
        by_steamid[str(player.steamid)] = player

    mapping: dict[str, Player] = {}
    errors: list[str] = []
    warnings: list[str] = []
    used: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            errors.append("fixture.players 中存在非对象条目")
            continue
        names: list[str] = []
        for key in ("name", "player", "nick", "steam_name"):
            value = entry.get(key)
            if isinstance(value, str) and value.strip():
                names.append(value.strip())
                break
        aliases = entry.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        if isinstance(aliases, list):
            names.extend(str(alias).strip() for alias in aliases if str(alias).strip())

        steamid = str(entry.get("steamid") or "").strip()
        found: Player | None = by_steamid.get(steamid) if steamid else None
        if found is None:
            for name in names:
                matches = by_name.get(_normal_key(name), [])
                if len(matches) == 1:
                    found = matches[0]
                    break
                if len(matches) > 1:
                    errors.append(f"fixture 选手 {name!r} 匹配到多个 demo 选手，请改用 steamid")
                    break
        label = names[0] if names else (steamid or "?")
        if found is None:
            errors.append(f"fixture 选手 {label!r} 在 demo 中找不到")
            continue
        if found.steamid in used:
            errors.append(f"fixture 选手 {label!r} 与 {found.name!r} 重复指向同一 steamid")
            continue
        used.add(found.steamid)
        mapping[label] = found

    missing = [player.name for player in players if player.steamid not in used]
    for name in missing:
        errors.append(f"fixture 缺少 demo 选手 {name!r}")
    if len(players) != len(entries):
        warnings.append(f"fixture 有 {len(entries)} 名选手，demo 有 {len(players)} 名选手")
    return mapping, errors, warnings


def validate_demo(
    demo: ParsedDemo,
    fixture_path: str | Path | None = None,
    *,
    required_fields: Iterable[str] = EXACT_FIELDS,
) -> ValidationResult:
    """Compare every demo player's exact fields with the fixture."""

    required = tuple(required_fields)
    path = find_fixture(demo, fixture_path)
    if path is None:
        return ValidationResult(
            status="unverified",
            title="精确字段未校验",
            message=(
                "没有找到与这个 demo 匹配的计分板 fixture。"
                "严格模式不会生成最终 HTML；请先按 README 填写 fixtures/ 下的 JSON。"
            ),
            demo_sha256=demo.sha256,
            total_players=len(demo.players),
        )

    fixture = _load_fixture_file(path)
    fixture_validated = fixture.get("validated_fields")
    if isinstance(fixture_validated, (list, tuple)):
        selected = tuple(
            str(item).strip()
            for item in fixture_validated
            if str(item).strip() in EXACT_FIELDS
        )
        if selected:
            required = selected
    adr_rounding = str(fixture.get("adr_rounding") or "").strip().lower()
    meta = fixture.get("demo") or {}
    if not isinstance(meta, dict):
        meta = {}
    expected_sha = str(meta.get("sha256") or "").strip().lower()
    if expected_sha and expected_sha != demo.sha256.lower():
        return ValidationResult(
            status="mismatch",
            title="fixture 与 demo 不匹配",
            message=(
                "fixture 记录的 demo SHA-256 与当前文件不同。"
                "不能用别的对局数字来校验这个 demo。"
            ),
            fixture_path=path,
            fixture=fixture,
            demo_sha256=demo.sha256,
            total_players=len(demo.players),
            errors=[f"fixture sha256={expected_sha}，当前 demo sha256={demo.sha256}"],
        )

    entries = _fixture_entries(fixture)
    mapping, errors, warnings = _resolve_players(demo, entries)

    checks: list[Check] = []
    missing_fields: list[str] = []
    for entry in entries:
        label = str(entry.get("name") or entry.get("player") or "?")
        player = mapping.get(label)
        if player is None:
            # Try to locate by the exact label through aliases for a better message.
            for mapped_label, mapped_player in mapping.items():
                if _normal_key(mapped_label) == _normal_key(label):
                    player = mapped_player
                    break
        if player is None:
            continue

        for field_name in required:
            expected = _expected_value(entry, field_name)
            if expected is None:
                missing_fields.append(f"{label}.{field_name}")
                continue
            if field_name in INT_FIELDS:
                try:
                    expected_int = int(expected)
                except (TypeError, ValueError):
                    errors.append(f"{label}.{field_name} 不是整数：{expected!r}")
                    continue
                actual_int = player.exact_int(field_name)
                checks.append(
                    Check(
                        player=player.name,
                        field=field_name,
                        expected=expected_int,
                        actual=actual_int,
                        ok=expected_int == actual_int,
                        detail="" if expected_int == actual_int else "计分板与解析值不同",
                    )
                )
                continue

            expected_dec = _to_decimal(expected)
            if expected_dec is None:
                errors.append(f"{label}.{field_name} 不是数字：{expected!r}")
                continue
            precision = _precision_for(fixture, field_name)
            rounding = ROUND_FLOOR if (field_name == "adr" and adr_rounding == "floor") else ROUND_HALF_UP
            actual_value = player.exact_float(field_name)
            expected_rounded = expected_dec.quantize(
                Decimal(1).scaleb(-precision), rounding=rounding
            )
            actual_rounded = _round_value(actual_value, precision, rounding)
            ok = expected_rounded == actual_rounded
            checks.append(
                Check(
                    player=player.name,
                    field=field_name,
                    expected=float(expected_rounded),
                    actual=float(actual_rounded),
                    ok=ok,
                    detail=(
                        ""
                        if ok
                        else f"按 {precision} 位小数比较：计分板 {expected_rounded}，解析 {actual_rounded}"
                    ),
                )
            )

    # Optional team score check.
    scores = fixture.get("team_scores") or fixture.get("score")
    if isinstance(scores, dict) and scores:
        for team in demo.team_list:
            expected_score = scores.get(team.name)
            if expected_score is None:
                continue
            try:
                expected_score_int = int(expected_score)
            except (TypeError, ValueError):
                errors.append(f"team_scores.{team.name} 不是整数")
                continue
            if expected_score_int != team.score:
                errors.append(
                    f"比分不一致：fixture {team.name}={expected_score_int}，解析={team.score}"
                )

    differences = [check for check in checks if not check.ok]
    for diagnostic in getattr(demo, "death_diagnostics", []) or []:
        detail = "、".join(
            f"第 {item.get('round_num')} 回合 {item.get('count')} 次"
            for item in diagnostic.get("rounds_detail", [])
        )
        warnings.append(
            f"{diagnostic.get('player')}：deaths={diagnostic.get('deaths')}，"
            f"正式回合={diagnostic.get('rounds')}；死亡明细：{detail or '无'}"
        )
    if errors or differences:
        status = "mismatch"
        title = "精确字段校验失败"
        message = (
            f"发现 {len(differences)} 项字段差异、{len(errors)} 项结构错误。"
            "已停止生成最终 HTML；请按差异报告修正 fixture 或解析。"
        )
    elif missing_fields:
        status = "partial"
        title = "精确字段部分校验"
        message = (
            f"fixture 缺少 {len(missing_fields)} 个必填字段，未校验项不能视为通过。"
            "严格模式不会生成最终 HTML。"
        )
    else:
        status = "verified"
        title = " / ".join(FIELD_LABELS.get(item, item.upper()) for item in required) + " 已校验"
        adr_precision = _precision_for(fixture, "adr")
        labels = " / ".join(FIELD_LABELS.get(item, item.upper()) for item in required)
        message = (
            f"{len(checks) // len(required) if required else 0} 名选手 × {len(required)} 项字段"
            f"（{labels}）与 fixture 完全一致"
            f"（{'ADR 取整' if adr_rounding == 'floor' else f'ADR {adr_precision} 位小数'}）。"
        )
        if "kast" not in required:
            message += " KAST 未在 fixture 中，未参与校验。"

    if missing_fields:
        errors.append("fixture 缺少必填字段：" + "、".join(missing_fields[:12]))
        if len(missing_fields) > 12:
            errors.append(f"……另有 {len(missing_fields) - 12} 个缺失字段")

    return ValidationResult(
        status=status,
        title=title,
        message=message,
        fixture_path=path,
        fixture=fixture,
        demo_sha256=demo.sha256,
        checks=checks,
        errors=errors,
        warnings=warnings,
        matched_players=len(mapping),
        total_players=len(demo.players),
    )


def format_result(result: ValidationResult, *, verbose: bool = True) -> str:
    lines = [f"状态：{result.status} — {result.title}", result.message]
    if result.fixture_path:
        lines.append(f"fixture：{result.fixture_path}")
    lines.append(f"demo sha256：{result.demo_sha256}")
    if result.errors:
        lines.append("错误：")
        lines.extend(f"  - {error}" for error in result.errors)
    if result.warnings:
        lines.append("警告：")
        lines.extend(f"  - {warning}" for warning in result.warnings)
    if verbose and result.checks:
        lines.append("")
        lines.append(f"{'选手':<28} {'字段':<10} {'计分板':>10} {'解析':>10}  结果")
        for check in result.checks:
            mark = "OK" if check.ok else "差异"
            lines.append(
                f"{check.player[:27]:<28} {FIELD_LABELS.get(check.field, check.field):<10} "
                f"{str(check.expected):>10} {str(check.actual):>10}  {mark}"
                + (f"  {check.detail}" if check.detail else "")
            )
    return "\n".join(lines)
