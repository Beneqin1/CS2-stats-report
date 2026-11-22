# 接手提示词：继续改进 CS2Stats

> 把下面整段交给下一个模型。它不需要看之前的聊天记录，但需要按顺序读仓库文件。

---

你是一名接手 **CS2Stats** 项目的资深 Python / Windows 桌面应用工程师。项目已经可用，当前版本 **1.1.0**，交付形态是 Windows `.exe`（PyInstaller onedir），用户是编程小白。你的任务是在**不破坏数据准确性**的前提下继续改进它。

请先做三件事：

1. 读 `README.md`（项目介绍、运行方式、数据口径）。
2. 读 `README.txt`（面向用户的中文完整手册、FAQ、路径、杀软说明）。
3. 读 `cs2stats/demo_parser.py`、`cs2stats/scoreboard.py`、`cs2stats/report_data.py`、`cs2stats/validation.py`、`cs2stats/demo_watch.py`、`cs2stats/gui.py`、`cs2stats/templates/report.html.j2`。

不要先改代码。先运行一次现有校验，确认基线：

```powershell
.\.venv\Scripts\python.exe -m compileall -q cs2stats run_app.py validate_stats.py tools
.\.venv\Scripts\python.exe validate_stats.py "<path-to-user-mirage.dem>" fixtures\user_mirage_20261122.fixture.json --quiet
.\.venv\Scripts\python.exe validate_stats.py _vendor\demos\dev_mirage.dem --quiet
```

预期：用户 Mirage demo 为 10 人 × 4 项 = 40 checks verified；dev demo 为 10 人 × 5 项 = 50 checks verified。demo 文件不在仓库里，需要用户提供，或从 awpy-fixtures 下载开发 demo。

## 项目背景

用户会通过 CS2 控制台 `map de_mirage` 开本地服务器，朋友 `connect` 加入；GOTV 需要在地图加载前启用。程序监听 `<CS2>\game\csgo\replays\`，发现新的 `.dem` 后等文件稳定，解析并生成 HLTV 风格 HTML。

当前报告包含：

- Match Stats：K-D、+/-、K/D、K(HS)、D、A、KAST、Open Kills、ADR、Swing、Rating；All/T/CT 三态。
- Performance Rating 横向条：全局 Rating 降序，三档颜色。
- Kill Matrix：事件可靠时显示；不可靠时整块隐藏。
- 目标选手雷达图 + 对比条形图。
- 头部大比分 + MVP；底部统一口径说明。

## 数据管线（关键，别搞反）

```text
awpy Demo
  → demo_parser.load_demo()
      → Round 过滤（formal rounds）
      → events 重建 raw Counters（kills/deaths/...）
      → ParsedDemo（exact 初始为事件值）
  → scoreboard.apply_scoreboard(parsed, demo_path)
      → demoparser2.parse_ticks()
      → 最终 tick 计分板 K/D/A/damage/headshot
      → 每回合总分差值 → KAST / 多杀 / side 计数
      → 覆盖 player.exact（精确层）
      → 事件与计分板不一致时降级：
          event_reliable=False
          matrix 清空
          opening/headshot 可用性按来源设置
          swing 用每回合 K/A/D fallback
  → report_data.build_report()
  → render.py + report.html.j2 生成单文件 HTML
```

### 必须遵守的规则

1. **K/D/A/ADR 的精确来源是 `demoparser2` 计分板属性**，不是 `player_death`。原因：某些 demo 有 bot 接管（`bot_takeover`），`player_death` 的 attacker/victim 会错位，曾出现 Beneqin1 43/30 而计分板是 23/15。
2. ADR 用 `damage_total / 正式回合数`，按截图口径 **floor**（向下取整）；不要改成四舍五入。
3. 不要为了“看起来完整”而把 KAST、Open Kills、Kill Matrix 标成精确。截图没有 KAST/Open Kills，无法 fixture 校验。
4. 事件层不可靠时，Kill Matrix 必须隐藏，不能输出全 0 矩阵冒充“没有击杀”。
5. `player_death` 只在 `event_reliable=True` 时用于 Kill Matrix / A(f) / D(t)；不要在不可靠 demo 上重新启用。
6. Rating / Swing / MVP 是近似值，HTML 必须保留“非 HLTV 官方数值”的脚注。
7. 改动精确层后，必须重新跑用户 fixture 和 dev fixture；不要只跑单元测试。
8. 当前 HTML 模板是用户确认过的成品，改 UI 前先征得用户同意；不要为了重构而重排颜色、表格列、脚注位置。

## 代码地图

| 文件 | 职责 |
|---|---|
| `cs2stats/demo_parser.py` | awpy 读取、正式回合过滤、事件重建、T/CT 分面、首杀/交易/多杀、ParsedDemo |
| `cs2stats/scoreboard.py` | demoparser2 计分板属性层、KAST 回合差分、Swing fallback、可用性/降级 |
| `cs2stats/validation.py` | fixture 校验、`validated_fields`、ADR floor、错误报告 |
| `cs2stats/report_data.py` | 报表 JSON：players/teams/radar/compare/matrix/meta/MVP |
| `cs2stats/rating.py` | 近似 Rating / Impact / Swing 公式 |
| `cs2stats/render.py` | Jinja2 + ECharts 内嵌 |
| `cs2stats/templates/report.html.j2` | 单文件报告的 HTML/CSS/JS |
| `cs2stats/demo_watch.py` | 监听 demo 文件夹、稳定等待、去重、报告生成 |
| `cs2stats/first_run.py` | `stat.cfg` / `settings.json` / 首次运行 |
| `cs2stats/paths.py` | `output/`、`settings.json`、onedir 根目录 |
| `cs2stats/gui.py` | Tkinter 双标签页 |
| `cs2stats/cli.py` | `--watch`、`--watch-once`、手动 demo、`--gsi`（旧实验功能） |
| `validate_stats.py` | 独立 fixture 校验脚本 |
| `build_exe.ps1` | onedir PyInstaller 打包 |

## 当前已知技术债 / 可改进点

按优先级：

1. **测试**：目前没有自动化测试目录。建议加 `tests/`，用 tmp_path 覆盖：
   - `Round.is_formal` 过滤；
   - `scoreboard._int` 对 64 位 SteamID 的精度；
   - ADR floor；
   - `validated_fields` 缺 KAST 不判 partial；
   - watcher 启动只处理最新一个、去重、稳定等待；
   - `paths.app_home` onedir/onefile/源码三态。
2. **KAST 含 traded**：当前只用每回合 K/A/S 差分，不含 traded。若能可靠拿到事件层，可加 traded 回合；但必须保留“事件不可靠时退回不含 traded”的降级。
3. **Kill Matrix 恢复**：bot 接管 demo 目前隐藏矩阵。可研究 `demoparser2` 的 tick 级 kills/deaths 差分、或解析 `player_death` 时用 `userid`→controller 映射修复归因；未验证前不要重新展示。
4. **Open Kills**：当前用计分板 `kills_total` 每回合首次增量推断，标“参考”。若能拿到可靠事件，再升级。
5. **打包体积**：onedir 约 378 MB，主要来自 polars/awpy/pandas/numpy/arrow。可尝试把计分板层拆成独立可选依赖，或改用轻量解析器；但不能破坏现有精确层。
6. **GUI**：Tkinter 可换 ttkbootstrap/PySide，但用户是小白，保持“双击→开始监听”的低认知负担。
7. **demo 自动定位**：目前 `<CS2>\game\csgo\replays` 自动定位 + 手动选择；可加“最近一次 GOTV 录制”提示，但不要擅自扫描全盘。
8. **旧 GSI 实验代码**：`gsi.py`、`gsi_config.py`、`tools/gsi_probe.py`、`gsi_fields_report.md` 是早期实验，当前主流程不依赖。若清理，先确认没有入口引用。
9. **fixtures 隐私**：仓库中的 `fixtures/user_*.json` 只有玩家昵称和 K/D/A，没有 SteamID；如果公开仓库，仍建议先征得用户同意，或改成匿名样例。

## 验收标准

每次改动至少跑：

```powershell
.\.venv\Scripts\python.exe -m compileall -q cs2stats run_app.py validate_stats.py tools
.\.venv\Scripts\python.exe validate_stats.py "<user-mirage.dem>" fixtures\user_mirage_20261122.fixture.json --quiet
.\.venv\Scripts\python.exe validate_stats.py _vendor\demos\dev_mirage.dem --quiet
.\.venv\Scripts\python.exe run_app.py --watch-once --no-open --fixture fixtures\user_mirage_20261122.fixture.json
```

并检查生成的 `output\report_*.html`：

- 标题/地图/比分正确；
- K/D/A/ADR 与 fixture 一致；
- KAST 标“推断”，Kill Matrix 要么准确、要么明确隐藏；
- All/T/CT 三态切换后表格、Performance、雷达、对比条同步；
- 没有 JS console 错误。

## 交付要求

- 保持 `--onedir` 为默认，`--onefile` 可选；
- 改完更新 `CHANGELOG.txt` 和 `README.txt`/`README.md`；
- 不要提交 `.venv/`、`_vendor/`、`package/`、`dist/`、`screenshots/`、用户 demo、日志；
- 如果改了精确层，必须明确说明“哪些字段变成精确、哪些仍推断”，不要模糊表述。

## 你接手后的第一件事

先向用户确认：

1. 这次想改什么（数据、UI、打包、还是稳定性）；
2. 是否能提供同一场对局的 Tab 计分板截图 + `.dem` 路径；
3. 是否允许改 HTML 模板（当前 UI 已定稿）。

然后再动手。不要把“能跑”当成“数据正确”，也不要把“近似”写成“官方”。
