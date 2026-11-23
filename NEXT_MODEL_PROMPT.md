# 接手提示词：继续改进 CS2Stats

> 把下面整段交给下一个模型。它不需要看之前的聊天记录，但需要按顺序读仓库文件。

---

你是一名接手 **CS2Stats** 项目的资深 Python / Windows 桌面应用工程师。项目已经可用，当前版本 **1.2.0**，交付形态是 Windows `.exe`（PyInstaller onedir），用户是编程小白。你的任务是在**不破坏数据准确性**的前提下继续改进它。

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
- Rating 分解表：KPR / DPR / KAST / Impact / ADR / Swing / econ Δ / Rating 逐项对照。
- Performance Rating 横向条：全局 Rating 降序，三档颜色。
- Kill Matrix：逐回合与计分板差分核对，仅保留一致回合；击杀/死亡双视角 + All / First kills / AWP / 手枪范围；attacker 为空的死亡归入“环境”行/列；无一致回合时整块隐藏。
- 目标选手雷达图 + 对比条形图。
- 头部渐变地图大标题 + 发光比分 + MVP 金卡；校验横幅 + 解析提示横幅；底部统一口径说明。

## 数据管线（关键，别搞反）

```text
awpy Demo
  → demo_parser.load_demo()      （先查 cache\；命中直接返回）
      → Round 过滤（formal rounds）
      → events 重建 raw Counters（kills/deaths/...）
      → kill_feed（正式回合全部击杀事件，attacker=None 表示环境/自杀）
      → ParsedDemo（exact 初始为事件值）
  → scoreboard.apply_scoreboard(parsed, demo_path)
      → demoparser2.parse_ticks()
      → 最终 tick 计分板 K/D/A/damage/headshot
      → 每回合总分差值 → KAST / 多杀 / side 计数
      → 覆盖 player.exact（精确层）
      → 逐回合核对：每回合 10 人的事件 K/D 与计分板差分完全一致
          → 该回合进 reliable_rounds；Kill Matrix 由这些回合重建
          → 不一致回合整体排除，回合号写入 matrix_coverage
      → 无任何一致回合时 matrix 隐藏（matrix_hidden）
      → KAST traded 只计 reliable_rounds
      → 自洽性检查 → consistency_warnings → 报告“解析提示”横幅
  → report_data.build_report()
  → render.py + report.html.j2 生成单文件 HTML
```

### 必须遵守的规则

1. **K/D/A/ADR 的精确来源是 `demoparser2` 计分板属性**，不是 `player_death`。原因：某些 demo 有 bot 接管（`bot_takeover`），`player_death` 的 attacker/victim 会错位，曾出现 Beneqin1 43/30 而计分板是 23/15。
2. ADR 用 `damage_total / 正式回合数`，按截图口径 **floor**（向下取整）；不要改成四舍五入。
3. 不要为了“看起来完整”而把 KAST、Open Kills、Kill Matrix 标成精确。截图没有 KAST/Open Kills，无法 fixture 校验。
4. Kill Matrix 只由 `reliable_rounds`（逐回合与计分板差分完全一致的回合）重建；不要把核对失败的回合混进去，也不要在没有任何一致回合时输出全 0 矩阵冒充“没有击杀”。
5. `player_death` 的归因以 SteamID 为主键；attacker 为空的死亡归入“环境”行/列，不得丢弃也不得冒充普通击杀。
6. Rating / Swing / MVP 是近似值（Rating 2.0 近似公式，系数在 `cs2stats/config.py`），HTML 必须保留“非 HLTV 官方数值”的脚注。
7. 改动精确层后，必须重新跑用户 fixture 和 dev fixture；不要只跑单元测试。
8. 解析缓存（`cs2stats/cache.py`）以 `CACHE_VERSION` + awpy/demoparser2 版本守卫；改动解析输出结构时必须递增 `CACHE_VERSION`，否则旧缓存会静默污染结果。

## 代码地图

| 文件 | 职责 |
|---|---|
| `cs2stats/demo_parser.py` | awpy 读取、正式回合过滤、事件重建、kill_feed、T/CT 分面、首杀/交易/多杀、ParsedDemo、缓存接入 |
| `cs2stats/scoreboard.py` | demoparser2 计分板属性层、KAST 回合差分（含 traded）、逐回合核对重建 Kill Matrix、自洽性检查、可用性/降级 |
| `cs2stats/validation.py` | fixture 校验、`validated_fields`、ADR floor、错误报告 |
| `cs2stats/report_data.py` | 报表 JSON：players/teams/radar/compare/matrix/meta/MVP/Rating 分解 |
| `cs2stats/rating.py` | Rating 2.0 近似公式 / Impact / Swing |
| `cs2stats/cache.py` | 解析结果缓存（sha256 + CACHE_VERSION + 解析器版本守卫） |
| `cs2stats/render.py` | Jinja2 + ECharts 内嵌 |
| `cs2stats/templates/report.html.j2` | 单文件报告的 HTML/CSS/JS |
| `cs2stats/demo_watch.py` | 监听 demo 文件夹、稳定等待、去重、报告生成 |
| `cs2stats/first_run.py` | `stats.cfg` / 旧版 cfg 清理 / `settings.json` / 首次运行 |
| `cs2stats/paths.py` | `output/`、`settings.json`、onedir 根目录 |
| `cs2stats/gui.py` | Tkinter 双标签页（HLTV 深色主题） |
| `cs2stats/cli.py` | `--watch`、`--watch-once`、手动 demo、`--no-cache` / `--clear-cache`、`--gsi`（旧实验功能） |
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
   - `paths.app_home` onedir/onefile/源码三态；
   - `cache` 的 CACHE_VERSION 失效路径。
2. **KAST traded 覆盖率**：traded 判定依赖事件层逐回合核对，核对失败的回合计入不了 traded；这是有意为之的保守口径，不要用全局事件开关放开。
3. **Kill Matrix 覆盖率**：矩阵只含核对一致回合。可研究 `demoparser2` tick 级差分修复事件归因本身（曾观察到击杀错记到队友头上），修好归因才能让 100% 回合进矩阵；未验证前不要扩大覆盖。
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
