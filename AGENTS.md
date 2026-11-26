# AGENTS.md — CS2Stats 项目记忆

维护协议：每次任务交付前更新本文件（条目已存在则更新，不重复追加）；每 3 个任务做一次流程自省，过时踩坑归档不删除。新任务先读本文件再动手。

## [约定] 硬性规则

- 数据准确第一：K/D/A/ADR 只认 demoparser2 计分板属性；KAST 是推断值；Rating / Swing / MVP 是近似值，UI 必须标注；事件层不可靠时隐藏 Kill Matrix，不为"完整感"硬凑。
- 改解析层（精确字段）后必须重跑用户 fixture 校验，须 verified：
  `PYTHONPATH=. ./.venv/Scripts/python.exe validate_stats.py --quiet <demo> <fixture>`
- 回归三件套：`./.venv/Scripts/python.exe -m compileall -q cs2stats`、`PYTHONPATH=. ./.venv/Scripts/python.exe -m unittest discover -s tests`、fixture 校验。
- 不自动 git push：只输出建议文件清单，用户 review 后手动 push。
- 不顺手重构不相关代码；不升级/更换依赖版本；修复必须对所有 demo 通用，禁止用 fixture 特定值打补丁。
- 解析失败 / 字段缺失 / demo 截断 → 报告里明确警告，禁止静默跳过。
- 每次交付末尾附"复盘"（改动是否最小化 / 红线检查 / 验证完整性）。

## [踩坑] 现象 → 根因 → 修法

- 实时监听一进图就报"dem 无法解析" → 监听到 .dem 创建就立刻解析，此时头部不完整的竞态 → 等文件大小稳定（两次采样不变）或录制结束再解析；不完整错误重试，单文件失败不退出监听。（v1.2.0）
- 监听竞态复发/判据太弱（v1.2.0 的"3 秒不变即就绪"会被 CS2 分块写入的数秒停笔间隙误判）→ v1.4.2 升级为三重判据：≥256KB + 连续 3 次采样（间隔 ≥3s）不变 + 出现更新 .dem 的辅助信号；解析失败按错误文本分类：`outside command stream`/`unexpected EOF`/`failed to fill`=还在录（指数退避 5→15→30s，最多 5 次），`magic bytes mismatch`/`snappy corrupt input`/未知=真损坏直接报错不重试；全部分类与采样值写日志。
- 机器人居多的 cbble demo 只显示 1 回合 → 文件本身只录了约 108 秒（1 完整回合+第 2 回合 4 秒），非解析 bug → 截断检测 + 报告"录制不完整"警告；回合切分不能只信 round 事件，非现役地图缺坐标数据时优雅降级、回合数不能错。（v1.3.1）
- "高级 / GOTV 配置"按钮无响应 → UI 事件绑定问题（v1.2.0 已修）。
- 注入 cfg 文件名混用 `stat_cs2stats.cfg` / `stats.cfg` → 统一为 `stats.cfg`，打包脚本清理旧名残留。（v1.2.0）
- 后台 bash 启动的 GUI 被 Windows 置 minimized（rect -21333），`ShowWindow(SW_RESTORE)` 只回到默认 placement（977×787）而非 Tk geometry，截图看起来像右侧裁剪 → 验证布局时先用 `SetWindowPos` 强制到目标尺寸再截图，别误判为 bug。PowerShell 调 ShowWindow 注意 `[System.IntPtr]::new($h.ToInt64())` 类型转换。
- **截图工具必须 DPI 感知**：PowerShell 截图脚本是 DPI 虚拟化进程，坐标空间是逻辑单位，对调用了 `SetProcessDpiAwareness` 的 Tk 窗口只能拍到左上 2/3——右侧控件"消失"、底部"被裁"都是截图假象（v1.4.0 自查时绕了一大圈）。修法：脚本开头 `SetProcessDpiAwareness(1)`，用 `EnumWindows` 按标题找窗口（Toplevel 弹窗不是进程主窗口），`PrintWindow` 抓物理像素。工具已固化在 `tools/win_capture.ps1`（用法：`-Title <标题片段> -Out <png>`）。
- 预览 harness 里 Toplevel 弹窗在主窗口 minimized 时 `grab_set()` 抛 "window not viewable"——真实使用主窗口可见，无此问题；写预览脚本时先恢复主窗口再开弹窗。
- 量化验证窗口尺寸：`update_idletasks()` 后 `winfo_reqwidth()/reqheight()` 返回逻辑像素；本机屏幕 1707x1067 逻辑（2560x1600@150%），内容需求必须 < 工作区。
- DPI 放大字体后窗口内容被截 → `_dpi_aware()` 必须在 `Tk()` 创建之前调用；构建后 `update_idletasks()` 按 `reqwidth/reqheight` 自动撑窗。（v1.3.2）
- ECharts radar 轴从 12 点方向起**逆时针**排列（不是直觉的顺时针）→ HTML 覆盖层按顺时针算角度会把标签贴到对称的另一侧（Rating 标签压住 Surviving 轴名）→ 覆盖层角度用 `(-90 - i*60)°`，且改完必须截图实证方向。（v1.4.1）
- 雷达爆表大标签直接放在顶点处会压住该轴的轴名小字（两者都在外环外同一径向上）→ 爆表轴的 ECharts 轴名置空、轴名并入覆盖层标签组（轴名/大数字/best 三行），锚点沿径向外推 52px。（v1.4.1）

## [知识] 关键事实

- 解析栈：awpy 3.x + demoparser2。Rating 2.0 近似公式（KAST 用百分数代入）：`0.0073·KAST + 0.3591·KPR − 0.5329·DPR + 0.2372·Impact + 0.0032·ADR + 0.1587`。
- GUI：HLTV 深色主题是 exe 端既定方向（v1.4.0 起，用户明确指定）；design token 全部集中在 `gui.py` 顶部（BG/PANEL/FIELD/TEXT/MUTED/ORANGE…），间距用 8px 栅格（8/12/16/22），正文 15 / 标题 26 / 主按钮 17。
- join 主键用 SteamID，不用昵称（改名 / 机器人 / 空名会导致 name 匹配失败）。
- bot 接管：同一 SteamID 人类 tick 与 bot tick 交替时，bot 时段数据剔除、不计入真人；Kill Matrix 保留 bot 维度；报告附 data_quality 摘要与"已清洗"标识。
- 非现役地图（cbble 等）无坐标数据：位置类功能降级并在报告标注"该地图无坐标数据"。
- `paths.py app_home()`：frozen 时若 exe 目录名不是 "cs2stats" 则返回 exe 目录本身 → 平铺 zip 解压后 output / stats.cfg / settings.json 自然落在 exe 同级，无需改 paths.py。
- `validation.py find_fixture()` 会搜索 exe 同级 `fixtures\` → 打包时 fixture 放包根 `fixtures\` 即可被 exe 自动发现。
- demoparser2 错误文本形态（实测）：截断文件报 `parse error: file-info header offset N outside command stream 16..M`（CS2 demo 尾部有 file-info 偏移表，截断即越界）；垃圾文件报 `invalid demo file: magic bytes mismatch (expected PBDEMS2\0)`；中段损坏报 `decompression error: snappy: corrupt input`。监听层的"可重试 vs 硬错误"分类就建立在这三类文本上（demo_watch.py `_TRANSIENT_HINTS`）。
- 基线 fixture：mirage demo `auto-20261122-1713-de_mirage-Beneqin1.dem`（14 回合，sha256 d536b7b1…）对应 `fixtures/user_mirage_20261122.fixture.json`；另一个 2313 mirage demo sha 不匹配该 fixture。
- 打包：`powershell.exe -NoProfile -ExecutionPolicy Bypass -File ./build_exe.ps1`（默认 onedir 平铺：zip 首层即 CS2Stats.exe；产物 `package\CS2Stats-x.y.z-onedir\` + `dist\*.zip`）。zip 在冒烟前打包；冒烟后清理包内运行产物（output/、settings.json、stats.cfg、cache/）。
- 报告雷达图固定刻度：`RADAR_PRO_AVG`（config.py）= 中环职业均值基准，外环 = 2× 基准固定不随数据自适应；爆表 = value>max 时 ECharts 线性外推绘制（不压平），峰值 >1.7× 时网格物理收缩（半径 78/峰值 %）；大数字标注走 HTML 覆盖层（`.burst-layer`），全场平均不参与爆表。Impact 基准 1.30 是按本项目公式（含首杀/多杀加成，比 HLTV 高约 0.2）校准的，勿照抄 HLTV 1.1。（v1.4.1）
- 版本发布固定动作：`config.py APP_VERSION` + `build_exe.ps1` 内版本号 + README/README.txt/CHANGELOG + `docs/releases/vX.Y.Z.md`。
- git 状态（2026-11-24）：本地 main 在 df6a151（v1.3.0）及其后改动，远端在 928c8f3；tag v1.3.0 仅本地。push 时连同新 commit 与 tag 一起。

## [禁止] 做错过的 / 高风险操作

- 禁止 git push（用户手动）。
- 禁止为让程序"能跑"静默丢弃数据或静默降级。
- 禁止升级 / 更换依赖版本。
- 禁止顺手重构与任务无关的代码。
- 禁止把推断字段（KAST、Rating、Swing、MVP）展示为精确官方数据。
- 禁止用 fixture / golden 文件的特定值硬编码修复解析问题。
