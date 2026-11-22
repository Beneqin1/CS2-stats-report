# CS2 GSI 字段探测报告

- 探测日期：2026-11-22
- 游戏：Counter-Strike 2（appid 730，GSI provider version 14200）
- 地图：de_mirage（本地 bot 对局）
- 配置：`gamestate_integration_cs2stats.cfg`，端口 32123，`buffer 0.1`、`throttle 0.2`、`heartbeat 10.0`
- 原始证据：`gsi_dumps/`（第一轮，正常游玩）、`gsi_dumps2/`（第二轮，正常游玩 + spectator + gameover）
- 探测工具：`tools/gsi_probe.py`
- 说明：本报告只描述**实际观测到的 GSI payload**，不把推测写成事实。

## 一、结论摘要

| 报告数据 | GSI 实测情况 | 实时模式可用口径 |
|---|---|---|
| Kills / Deaths / Assists | 正常游玩：只有本地玩家 `player.match_stats`；spectator：`allplayers[*].match_stats` 有全部 10 人 | 有 allplayers 时按选手聚合；否则只能显示本地玩家。未与计分板 fixture 校验前，标“GSI 聚合”，不标“已校验精确” |
| ADR | **没有累计伤害字段**。正常游玩 `player.state` 没有 `round_totaldmg`；spectator 的 `player.state` 和 `allplayers[*].state` 有每回合 `round_totaldmg` | 只有 spectator 能对每回合 `round_totaldmg` 求和；这是**原始回合伤害**，没有 `health_pre/health_post`，无法复刻 demo 的“单次命中封顶剩余生命”口径。标“GSI 回合伤害汇总（待 demo 校验）”，不能冒充精确 ADR |
| KAST | 没有 KAST 字段 | spectator 可用每回合击杀/助攻增量 + 回合结束是否存活推断；**无法判断 traded**（被交易），推断值偏低。标“推断，缺 traded” |
| Open Kills | 没有首杀字段 | spectator 可用每回合首个击杀增量推断；若两次快照之间发生多杀，无法确定哪一颗是首杀。标“参考/推断” |
| Kill Matrix | **没有 attacker/victim SteamID 配对** | 只能用相邻快照的 kills/deaths 增量推断；并发击杀会漏记或错配。标“推断”，不得标精确；无法推断时隐藏 |
| Rating / Swing | 没有 Rating/Swing 字段 | 沿用现有近似公式；Swing 依赖击杀时的存活人数差，GSI 没有该时序，实时模式不能可靠计算，应标“需 demo 补全”或用保守近似 |
| 比分 / 比赛结束 | `map.team_ct.score`、`map.team_t.score`；`map.phase="gameover"` | 比分按侧别读取，必须按 roster 映射队伍身份；gameover 有侧别/比分翻转坑，见下 |
| 回合边界 | `map.phase`、`map.round`、`round.phase` | 用 `map.phase=live` 和 `round.phase=freezetime/live/over`；`map.round` 在 over 后递增 |

**最重要的产品结论：正常游玩（activity=playing）拿不到 `allplayers`，因此拿不到其他 9 名选手。** 完整 10 人实时报告只能在 **spectator/GOTV** 模式下生成，或者在对局结束后用 demo 补全。普通游玩只能生成本地玩家摘要，必须如实说明，不能伪造队友/对手数据。

## 二、正常游玩 vs spectator

### 2.1 正常游玩

- 第一轮 `gsi_dumps` 和第二轮 `gsi_dumps2` 前 38 条 payload 中，`allplayers` 均为空（`players=0`）。
- 即使本地玩家死亡、回合结束（`round.phase=over`）、`activity=menu`，`allplayers` 仍然为空。
- 顶层只有 `provider`、`map`、`round`、`player`、`previously`、`auth`；没有可用的全场比赛数据。
- `player` 是本地玩家；`player.match_stats` 只有 `kills/assists/deaths/mvps/score`。
- `player.state` 字段：`health/armor/helmet/flashed/smoked/burning/money/round_kills/round_killhs/equip_value`；**没有 `round_totaldmg`**。

### 2.2 spectator

- 在控制台执行 `spectate` 后，下一条 payload 出现 `allplayers`，`players=10`。
- 此时 `player` 不再代表客户端本人，而是**当前观察目标**：
  - `player.steamid` / `player.name` 是观察目标；
  - `player.spectarget` 也是该目标的 SteamID；
  - `player.position` / `player.forward` 是观察相机/目标位置；
  - `player.activity` 可能出现 `textinput`；
  - 不能用 `player.steamid` 反推“客户端玩家”。
- `allplayers` 以 SteamID 为 key，每个选手包含：
  - `name`
  - `observer_slot`
  - `team`（`CT` / `T`）
  - `state`：`health/armor/helmet/defusekit/flashed/smoked/burning/money/round_kills/round_killhs/round_totaldmg/equip_value`
  - `match_stats`：`kills/assists/deaths/mvps/score`
  - `weapons`
  - `position` / `forward`
- 证据：`gsi_dumps2/payload_0039_113028_live_live.json`，`allplayers` 共 10 人；`payload_0111_113051_intermission_over.json` 回合结束时仍有 10 人。
- **没有 attacker/victim 配对**，也没有逐条击杀事件。

## 三、ADR：`round_totaldmg` 的真实含义与限制

### 3.1 字段存在性

- 正常游玩：`player.state` **没有** `round_totaldmg`。
- spectator：`player.state` 有；`allplayers[*].state` 也有。
- `match_stats` 在任何模式下都没有累计伤害字段。

### 3.2 行为

- `round_totaldmg` 在回合内随伤害累加，回合结束后保留该回合值，下一回合/下一场重置。
- 证据：`gsi_dumps2/payload_0111_113051_intermission_over.json`（`intermission/over`）中，10 名选手的 `round_totaldmg` 求和为 **846**，同一 payload 的 `match_stats` 合计为 **8 kills / 8 deaths / 2 assists**；7 名选手伤害非零。
- 下一条 `gameover` payload（`payload_0139_113059_gameover_freezetime.json`）中，`round_totaldmg` 全部为 0。
- 第一轮正常游玩 payload 的 `player.state` 中不存在该字段。

### 3.3 不能直接当精确 ADR 的原因

- GSI 不提供每次命中的 `health_pre` / `health_post`，也没有武器/命中部位/护甲信息。
- demo 的 ADR 口径是“单次命中最多扣除受害者剩余生命值”；GSI 的 `round_totaldmg` 是游戏内部回合伤害累计值，是否采用同一封顶逻辑**未经验证**。
- 846 点伤害 / 8 次击杀只能说明总伤害可能高于“800 点满血击杀上限”，不能单独证明它是 raw damage；但也不能证明它与 demo 完全一致。
- 因此实时报告只能显示：
  - `GSI 回合伤害汇总 / 回合数`，并标注“待 demo 校验”；
  - 或在对局结束找到本场 demo 后，用 demo 的精确 ADR 替换。
- **禁止**用 K/D/A 估算 ADR。

## 四、回合、半场与比赛结束

### 4.1 正常时间线

实测顺序：

```
menu（只有 provider/player/auth）
  → warmup（map 出现，round 缺失/0）
  → live + round.phase=freezetime
  → live + round.phase=live
  → round.phase=over（round.win_team 出现）
  → intermission（round.phase=over）
  → gameover（round.phase=freezetime）
  → warmup / menu（下一场）
```

- `map.phase`：`warmup` / `live` / `intermission` / `gameover`。
- `round.phase`：`freezetime` / `live` / `over`；热身/菜单时可能缺失。
- `map.round`：第一回合为 0；`round.phase=over` 后递增。
- `round.win_team`：只在 `round.phase=over` 时出现，值为 `CT` / `T`。
- `round.bomb`：可出现 `planted` 等状态。
- `map.team_ct.score` / `map.team_t.score`：按**当前侧别**计分，不是按固定队伍计分。

### 4.2 gameover 的比分/阵营翻转坑

实测（`gsi_dumps2`）：

| payload | map.phase | team_ct.score | team_t.score | player.team | round |
|---|---|---:|---:|---|---|
| 0024 | intermission | 1 | 0 | CT | over |
| 0026 | gameover | 0 | 1 | T | freezetime |
| 0027–0028 | gameover | 0 | 1 | T | freezetime |

- `gameover` payload 把 `team_ct/team_t` 的比分和 `player.team` **翻转**了。
- `previously.map.team_ct.score=1`、`team_t.score=0`，证明比赛实际是 CT 1:0。
- 因此最终比分必须：
  1. 保存 gameover 之前最后一条 `live/over/intermission` 的比分；或
  2. 使用 `previously.map` 的比分；
  3. 不要直接使用 gameover payload 的 `team_ct/team_t`。
- `gameover` 会按 heartbeat 重复推送（约 10 秒一次，实测 0026/0027/0028 三条）。去抖后只生成一次报告。

### 4.3 半场

- 本轮探测没有完整打到半场，**未直接观测到 halftime payload**。
- 但 `team_ct/team_t` 是侧别，半场必然换边；实现时必须用 roster（SteamID 集合）跟踪队伍身份，不能把 `team_ct` 永远当同一支队伍。
- 已观测到 gameover 的侧别翻转，说明“侧别会变”是实际存在的，不能假设固定。

## 五、K/D/A 与比分

- `match_stats.kills/assists/deaths` 是**整场累计**整数；在 spectator 下 10 人都有。
- 同一场比赛中单调递增；没有发现回退（除了新比赛重置）。
- `match_stats.score` 是 HLTV 风格的贡献分，不是比分；不要拿它当回合比分。
- `map.team_ct.score` / `map.team_t.score` 是回合比分。
- 玩家死亡后，`allplayers` 仍然保留其 `match_stats`；但 `state.health=0`，`round_totaldmg` 仍保留。
- 不能假设 `sum(kills) == sum(deaths)`：teamkill/自杀可能让 deaths 大于 kills；只做合理性提示，不做硬断言。

## 六、实时模式可实现/不可实现清单

### 6.1 可以作为“GSI 聚合”显示

- 本地玩家 K/D/A（正常游玩）。
- spectator 下 10 人 K/D/A。
- 比分（按 roster 映射队伍身份，gameover 用前一帧比分）。
- 回合数（按 `map.round`/`round.phase` 计数）。
- 每回合 `round_totaldmg`（spectator）→ “GSI 伤害/回合”，待 demo 校验。
- 每回合击杀/助攻/存活增量 → 推断 KAST（缺 traded）。
- 每回合首个击杀增量 → 推断 Open Kills（可能漏/错）。
- 每回合击杀/死亡增量 → 推断 Kill Matrix（可能漏/错）。
- 现有近似 Rating/Swing；Swing 没有击杀时序，实时模式应标“需 demo 补全”或保守处理。

### 6.2 必须标“不可用/需 demo 补全”

- 精确 ADR（没有 health_pre/health_post）。
- 精确 KAST（没有 traded 标记）。
- 精确 Open Kills（没有首杀事件）。
- 精确 Kill Matrix（没有 attacker/victim 配对）。
- 精确 Rating / Swing（公式本身是近似；Swing 时序缺失）。
- 队伍名称（GSI 没有队名；只能用 Team A/Team B 或本地玩家侧别）。

## 七、实现建议

1. **双来源**
   - 本地游玩：只生成本地玩家摘要；明确提示“GSI 只提供本地玩家，完整 10 人请用 spectator 或对局后 demo”。
   - spectator：生成 10 人报告；Kill Matrix/Open Kills/KAST/ADR 均按上面的分层标注。
2. **结束触发**
   - 监听 `map.phase == "gameover"`；保存最后一帧 live/over 的比分和全部选手 `match_stats`；去抖，只生成一次。
   - 报告生成后尝试按配置目录 + 最近修改时间找本场 `.dem`；找到则用 demo 解析补全精确 ADR/KAST/Open Kills/Kill Matrix，并在 HTML 顶部标注“已用 demo 补全，未用计分板 fixture 校验”。
3. **缺口处理**
   - 如果两帧间隔超过阈值（建议 15 秒）或 `allplayers` 突然从 10 变 0，标记“数据可能不完整”；不要静默补齐。
4. **报告标注**
   - 精确层：仅 K/D/A（且未 fixture 校验时标“GSI 聚合”）。
   - 近似/推断层：ADR（GSI 伤害）、KAST、Open Kills、Kill Matrix、Rating、Swing 全部带脚注。
   - 普通游玩模式：顶部横幅写明“仅本地玩家，非完整比赛报告”。

## 八、原始证据索引

- `gsi_dumps2/payload_0024_112830_intermission_over.json`：正常游玩，`allplayers=0`，`player.state` 无 `round_totaldmg`。
- `gsi_dumps2/payload_0026_112838_gameover_freezetime.json`：gameover 比分/阵营翻转，`previously` 保留正确比分。
- `gsi_dumps2/payload_0039_113028_live_live.json`：`spectate` 后 `allplayers=10`，含 `round_totaldmg`。
- `gsi_dumps2/payload_0111_113051_intermission_over.json`：回合结束，10 人 `round_totaldmg` 合计 846，K/D/A 合计 8/8/2。
- `gsi_dumps2/payload_0139_113059_gameover_freezetime.json`：gameover，`round_totaldmg` 重置为 0。
- `gsi_dumps2/manifest.jsonl`：每条 payload 的摘要清单。

## 九、未验证/待确认

- 半场 halftime 的真实 payload（本轮未打到）。
- `round_totaldmg` 与 demo `damages` 的逐回合/整场数值是否一致（需要用户 demo + fixture）。
- `allplayers` 在 GOTV/回放/不同比赛模式下是否行为一致。
- 不同 CS2 版本字段是否变化（本报告只对 version 14200 负责）。
- 使用 `round_totaldmg` 推导的 KAST/Open Kills/Kill Matrix 误差率（需要多场 demo 对照）。

## 十、当前实现（1.0.0）

- 实时监听入口：`CS2Stats.exe` 默认打开“实时监听”标签页；`--gsi` 提供无界面模式。
- 结束触发：只认 `map.phase=gameover`；`intermission` 不启动新回合；gameover 只生成一次报告。
- 去抖：`LiveSession` 用 `finishing` 标志，生成期间不再接收第二个 match_end。
- 比分：保存结束前最后一条非 gameover 的比分；不采用 gameover 的翻转值。
- 人员：normal play 只有本地 `player`；spectator 的 `allplayers` 覆盖 10 人；观察者本人不在
  allplayers 时会被过滤，不混入报告。
- ADR：spectator 每回合 `round_totaldmg` 取最大值后求和；报告标“GSI 回合伤害汇总，待 demo 校验”。
- KAST：K/A/S（存活）推断，缺 traded；报告标推断。
- Open Kills / Kill Matrix：相邻快照 kills/deaths 差分；多于 1 个并发击杀时跳过并在
  `meta.incomplete` 标注“同一快照内出现多杀，击杀配对可能缺失”。
- AWP Kill Matrix：GSI 没有可靠武器归因，实时视图隐藏。
- demo 补全：只在“地图、正式回合数、最终比分”三项都与 GSI 一致时才使用；否则
  拒绝补全并在报告标注“最近 demo 与 GSI 对局不一致”。这条检查是为了防止把上一场
  的 demo 误当本场数据。
- 精确层：实时模式只把 K/D/A 标为 GSI 聚合；未与计分板 fixture 校验前，不称“已校验精确”。
