# CS2 Stats Report

**本地监听 CS2 GOTV Demo，自动生成 HLTV 风格 HTML 战报的 Windows 小工具。**

![全板块报告截图（占位）](screenshots/report-full.png)
> 占位：一张包含所有板块的完整报告截图（screenshots/report-full.png）

## 特性

- **HLTV 风格报告**：深色底、橙白配色，Match Stats / Performance Rating / MVP 头部一应俱全
- **Kill Matrix 击杀矩阵**：5×5 对位击杀/死亡热力表，支持 All / First kills / 残局 1vX / AWP / 手枪维度，环境/自杀归入"环境"行列
- **Rating 分析**：KPR / DPR / KAST / Impact / ADR 分项对照表，Rating 2.0 近似公式各项可独立核对
- **雷达图与对比**：目标选手六维雷达（固定职业基准刻度，超标顶点"爆表"冲出网格并金色发光标注）+ 与全场平均的条形对比
- **Style DNA 打法风格指纹**：六维风格雷达 + 原型分类（突破手/自由人/辅助/狙击核心/Anchor）+ 与职业选手画像的相似度对标
- **教练建议引擎**：锚定具体地图/位置/动作的规则化建议（严重度分级、数据依据可展开）
- **高光回合定位**：自动识别残局 1vX / ACE 多杀 / ECO 翻盘等高光回合，一键复制 `playdemo` 跳转指令
- **本地离线运行**：单文件 HTML，图表/数据全部内嵌，不联网
- **实时监听**：监听 `replays\` 目录，打完一局自动出报告；也支持手动选择已有 `.dem`

## 免责声明（数据口径）

- 本工具的 **Rating 为基于公开拟合公式的近似计算**：
  `Rating ≈ 0.0073×KAST + 0.3591×KPR − 0.5329×DPR + 0.2372×Impact + 0.0032×ADR + 0.1587`（KAST 以百分数代入），**并非 HLTV 官方数据**。与 HLTV 官方 Rating 2.0 的偏差 **< 0.05 属正常范围**；报告附 Rating 分解表便于交叉核对。
- **K/D/A/ADR 精确层**来自 `demoparser2` 计分板属性，并可用计分板截图 fixture 逐项校验；**KAST / Swing / MVP 为推断/近似值**，报告中均有明确标注。
- **人机局（bot 对局）支持**：bot 与真人以 SteamID 区分聚合；检测到"玩家接管 bot"时自动清洗接管时段的事件归因，报告顶部展示"数据可信度"标签与清洗摘要。离线 bot 对局的回合事件可能不完整，本工具以 freeze time / 重生等游戏状态事件补充检测，录制不完整的回合不会计入统计，并会在报告中明确警告。
- **非现役地图（如 cbble/cache 等）支持**：地图本身不影响解析（坐标、事件均来自 demo 本身）；但请注意 GOTV 录制可能对非现役地图不稳定——若 `.dem` 文件本身只录制了对局的一部分，报告会明确标注"录制不完整"，而不是假装对局只有一回合。

## 快速开始

1. 下载并完整解压 `CS2Stats-1.4.2-onedir.zip`。
2. 双击解压出来的 `CS2Stats.exe`——首次启动会**自动注入 `stats.cfg`** 到 CS2 的 `game\csgo\cfg`（定位失败时会请你手动选择目录）。
3. 打开 CS2 控制台，**在加载地图之前**执行：

   ```text
   exec stats
   map de_mirage
   ```

   `tv_enable 1` / `tv_autorecord 1` 必须在地图加载前生效，中途设置对当前地图无效。

4. 正常打一局。
5. 对局结束、录制文件写完后，程序**自动解析并用浏览器打开报告**（保存在 exe 同级 `output\report_<地图>_<时间戳>.html`）。

也可以手动解析已有 demo：

```powershell
.\CS2Stats.exe "D:\demos\my_match.dem"
```

## 技术栈与工作原理

**技术栈**：Python 3.12 · [awpy](https://github.com/pnxenopoulos/awpy) / [demoparser2](https://github.com/LaihoE/demoparser)（demo 解析）· Tkinter（GUI）· Jinja2 + ECharts（单文件 HTML 报告）· PyInstaller（打包）

**工作流程**：

```text
CS2 GOTV 录制 (.dem)
      │
      ▼
awpy / demoparser2 解析 ──► 事件表 + 计分板属性
      │
      ▼
分析层（demo_parser / scoreboard / extras）
  · 正式回合过滤（含录制截断检测、bot 接管清洗）
  · 精确层重建 + 逐回合自洽核对，不一致即降级并标注
      │
      ▼
报告数据（report_data / rating / style_dna / coach / highlights）
      │
      ▼
Jinja2 渲染 ──► 单文件 HTML 报告（ECharts 内嵌，离线可看）
```

解析结果按 demo 的 SHA-256 缓存在 `cache\`，同一文件不会重复解析。

## 本地构建

```powershell
git clone https://github.com/Beneqin1/CS2-stats-report.git
cd CS2-stats-report
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1
```

产物（zip 为平铺结构，解压后即可看到 CS2Stats.exe）：

```text
package\CS2Stats-1.4.2-onedir\CS2Stats.exe
dist\CS2Stats-1.4.2-onedir.zip
```

从源码直接运行：`.\.venv\Scripts\python.exe run_app.py`（GUI）或 `run_app.py --watch`（监听）/ `run_app.py <demo>`（手动解析）。

## Roadmap

| 功能 | 状态 |
|---|---|
| Kill Matrix（多维度 + 热力表） | ✅ 已实现 |
| Rating 2.0 近似 + 分解表 | ✅ 已实现 |
| 风格指纹 Style DNA（原型分类 + 职业选手相似度） | ✅ 已实现（画像库持续校准） |
| 高光回合识别 + 回放跳转指令 | ✅ 已实现 |
| 教练建议引擎（规则化、可插件化） | ✅ 已实现（规则库扩充中） |
| Round Swing / WPA 逐回合可视化 | 🔜 规划中（数据层已就绪） |
| 心态稳定性曲线（连败/关键回合切片） | 🔜 规划中 |
| 位置热力图 | 💡 探索中 |

## 截图

> 占位（`screenshots/` 目录，后续补充）：
>
> - `screenshots/report-full.png` — 全板块完整报告
> - `screenshots/match-stats.png` — Match Stats
> - `screenshots/kill-matrix.png` — Kill Matrix
> - `screenshots/radar.png` — 雷达图与对比
> - `screenshots/style-dna.png` — Style DNA
> - `screenshots/gui.png` — 程序主界面

## License

MIT
