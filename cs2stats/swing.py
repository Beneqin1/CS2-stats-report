"""Offline heuristic WPA analysis; never changes scoreboard statistics."""
from __future__ import annotations

import math
from collections import defaultdict


def win_probability(alive_t, alive_ct, econ_t, econ_ct, planted=False, seconds_left=115):
    if alive_ct == 0:
        return 1.0
    if alive_t == 0 and not planted:
        return 0.0
    logit = -0.12 + 0.55 * (alive_t - alive_ct)
    if econ_t > 0 and econ_ct > 0:
        logit += 0.45 * math.log(max(0.25, min(4, econ_t / econ_ct)))
    if planted:
        logit += 0.85
    elif seconds_left < 25:
        logit -= 0.9 * (1 - max(0, seconds_left) / 25)
    return 1 / (1 + math.exp(-logit))


def round_wpa(roster, kills, bombs, live_tick, tickrate, econ_t, econ_ct):
    alive = dict(roster)
    result = defaultdict(float)
    planted = False
    timeline = [(int(e['tick']), 0, e) for e in kills]
    timeline += [(int(e['tick']), 1, e) for e in bombs]

    def probability(tick):
        return win_probability(sum(s == 'T' for s in alive.values()),
                               sum(s == 'CT' for s in alive.values()), econ_t, econ_ct,
                               planted, 115 - (tick - live_tick) / tickrate)

    def credit(sid, delta):
        if sid in roster:
            result[sid] += delta if roster[sid] == 'T' else -delta

    for tick, kind, event in sorted(timeline, key=lambda e: (e[0], e[1])):
        before = probability(tick)
        if kind:
            actor = int(event.get('steamid') or 0)
            if event.get('event') == 'finish_plant' and not planted:
                planted = True
                credit(actor, probability(tick) - before)
            elif event.get('event') == 'defuse':
                credit(actor, -before)
            continue
        victim, attacker = event['victim'], event.get('attacker')
        if victim not in alive:
            raise ValueError(f'重复死亡或未识别玩家：{victim}')
        del alive[victim]
        delta = probability(tick) - before
        if attacker != victim and attacker in roster:
            credit(attacker, delta)
            if roster[attacker] != roster[victim]:
                credit(victim, delta)
        else:
            credit(victim, delta)
    return dict(result)


def analyse_match(demo):
    """Return per-player/per-side average WPA percentage points and provenance."""
    unavailable = {'available': False, 'source': '自研启发式 WPA（非 HLTV）'}
    if not demo.data_quality.get('event_layer_reliable', False):
        return {}, {**unavailable, 'warning': '事件归因未通过核对，Swing 隐藏；不使用计分板代理伪装 WPA。'}
    try:
        from awpy import Demo
        bombs = Demo(str(demo.path)).bomb.to_dicts()
    except Exception as exc:
        return {}, {**unavailable, 'warning': f'bomb 事件读取失败，Swing 隐藏：{exc}'}
    sums = defaultdict(lambda: defaultdict(float))
    counts = defaultdict(lambda: defaultdict(int))
    try:
        for rnd in demo.counted_rounds:
            roster = {p.steamid: demo.side_for(rnd.index, p.steamid) for p in demo.players}
            if any(s not in ('T', 'CT') for s in roster.values()):
                raise ValueError(f'第 {rnd.round_num} 回合阵营缺失')
            start = rnd.freeze_end_tick or rnd.live_start
            end = rnd.end_tick or rnd.official_end_tick
            if start is None or end is None:
                raise ValueError(f'第 {rnd.round_num} 回合时间边界缺失')
            econ_t = demo.economy.get((rnd.round_num, 'T'), 0)
            econ_ct = demo.economy.get((rnd.round_num, 'CT'), 0)
            if econ_t <= 0 or econ_ct <= 0:
                raise ValueError(f'第 {rnd.round_num} 回合装备价值缺失')
            kills = [e for e in demo.kill_feed if e['round_index'] == rnd.index and e['tick'] <= end]
            values = round_wpa(roster, kills, [e for e in bombs if start <= e['tick'] <= end],
                               start, demo.tickrate, econ_t, econ_ct)
            for sid, side in roster.items():
                for scope in ('ALL', side):
                    sums[sid][scope] += values.get(sid, 0)
                    counts[sid][scope] += 1
    except (ValueError, KeyError, TypeError) as exc:
        return {}, {**unavailable, 'warning': f'WPA 输入不完整，Swing 隐藏：{exc}'}
    values = {sid: {scope: 100 * value / counts[sid][scope] for scope, value in scopes.items()}
              for sid, scopes in sums.items()}
    return values, {'available': True, 'source': '自研启发式 WPA（经济/人数/下包/时间；非 HLTV）',
                    'definition': '个人事件胜率变化总和 / 正式回合数 × 100；不为对齐截图另乘校准系数。'}
