CS2 数据报告 1.3.1
======================================================================
把一张地图的 CS2 .dem 解析成 HLTV 风格的 HTML 报告。

默认用法是“自动监听 demo 文件夹”：
  开游戏前双击 CS2Stats.exe，指定 GOTV demo 输出目录并开始监听；
  一局结束后，GOTV 自动写出的 .dem 被程序发现，解析完成后自动弹出报告。

也可以手动选择已有 .dem。最终交付物是 Windows 程序，你不需要安装 Python，
也不需要命令行。


一、自动监听 Demo（默认模式）
----------------------------------------------------------------------
1. 解压 onedir 压缩包，进入 CS2Stats 文件夹，双击 CS2Stats.exe。
   默认标签页是“自动监听 Demo（默认）”。

2. 第一次启动会自动生成 stats.cfg 并尝试复制到 CS2 的 cfg 目录。
   - 文件位置：CS2Stats-1.3.1-onedir\stats.cfg（onedir 中在 exe 的上一级；
     onefile 中与 exe 同级）。
   - 旧版本生成的 stat.cfg / stat_cs2stats.cfg，在确认是本程序生成的
     内容后会自动清理，避免旧文件残留。
   - 自动定位失败会弹出目录选择；取消后主界面仍保留“配置 stats.cfg…”按钮。
   - 开图前在 CS2 控制台执行提示的命令（exec stats）。
   详见下面第二节。

3. “Demo 输出目录”默认指向 CS2 的 game\csgo\replays 目录，例如：
       D:\program files\steam\steamapps\common\
       Counter-Strike Global Offensive\game\csgo\replays
   程序自动定位；找不到时点“浏览…”选择，并会记住到 exe 同级的 settings.json。
   如果 replays 目录还不存在，首次开图后 CS2 会自动创建，程序不会乱建目录。

4. “报告输出目录”固定为程序根目录的 output\，例如：
       CS2Stats-1.3.1-onedir\output\
   对局结束、demo 解析完成后，程序在这里生成
       report_<地图>_<日期>_<时间>.html
   并用默认浏览器自动打开。目录不存在时程序自动创建。

5. 点“开始监听”。之后正常开本地服务器、打一局。对局结束、GOTV 写完 demo，
   程序会先等文件大小连续 3 秒不变，再解析，避免读到半成品。

6. 同一文件只处理一次；程序重启后也不会重复处理已经生成过报告的 demo。
   启动监听时，只把“当时最新的一个 .dem”作为候选，更早的旧 demo 不会
   批量补跑。想补跑某一场，用手动 Demo 模式选择它。

GOTV 录的是整场 demo，解析出来自然包含全部 10 人，不需要 spectator。
这张报告只针对单张地图，不做跨图汇总。


二、先启用 GOTV（这一步顺序很重要）
----------------------------------------------------------------------
控制台 map 命令开的本地服务器也支持 GOTV，不需要专用服务器。
但 GOTV 必须在地图加载之前启用；地图已经加载后再设置，对当前地图无效。

正确顺序（在 CS2 控制台里）：
    1) tv_enable 1
    2) tv_autorecord 1
    3) map de_mirage
       （或者通过“开始 → 练习/竞技”加载地图）

说明：
  - tv_enable 1 让服务器启用 GOTV；该设置通常在下一次地图加载时生效。
  - tv_autorecord 1 让 GOTV 自动录像；每张地图结束/切换时写出 .dem。
  - 已经在地图里才输入这两条，当前地图通常不会开始录像；请换图或重开地图，
    再按上面的顺序操作。
  - 如果你习惯先 exec 自己的练习 cfg，也请先执行 GOTV 两条命令，再执行
    map 命令。

程序首次运行会自动生成 stats.cfg，省得每次手打：
  - 本地文件：exe 同级的 stats.cfg
        tv_enable 1
        tv_autorecord 1
  - 程序尝试复制到：<CS2>\game\csgo\cfg\stats.cfg
  - 旧版本生成的 stat.cfg / stat_cs2stats.cfg 会被自动清理
    （仅当内容确认是本程序生成的两行命令；外来的同名文件不会被动）。
  - 然后在 CS2 控制台、加载地图之前执行：
        exec stats
  - 自动定位失败时，主界面有“配置 stats.cfg…”按钮，手动选择 game\csgo\cfg。
    也可以把 exe 同级的 stats.cfg 手动复制过去。
  - 高级/兼容：主界面“高级 / GOTV 配置”仍可生成 cs2stats_gotv.cfg，
    用于不想使用 stats.cfg 的情况；执行 exec cs2stats_gotv。

注意：程序不会在你已经进入地图后偷偷改 tv_enable；配置只负责把命令写好，
执行顺序仍要由你在加载地图前完成。


三、手动选择 Demo（原有模式）
----------------------------------------------------------------------
1. 打开“手动选择 Demo”标签页。
2. 点“浏览…”选择 .dem。
3. “目标选手”默认 Beneqin1；报告下半部分会显示该选手的雷达图和对比条。
   找不到这个名字时，程序回退为全场 Rating 最高的选手，并在报告里提示。
4. 可选“计分板 fixture”；留空时自动查找，找不到也会生成报告（顶部标“未校验”），找到但不一致会停止生成。
5. 报告固定写到程序根目录的 output\，文件名包含地图和时间戳。
6. 点“开始生成报告”。成功后自动打开 HTML。

报告是单文件 HTML：CSS、JavaScript、ECharts 全部内嵌，断网也能看。
页面里可以切换 All / T / CT，主表、Performance 条形图、Kill matrix、
雷达图和对比条形图一起联动。


四、数据口径：哪些是精确的，哪些是参考/近似
----------------------------------------------------------------------
精确层（计分板属性，来自 demoparser2）：
  - Kills / Deaths / Assists / ADR / K (HS)。
  - 读取最终 tick 的 kills_total / deaths_total / assists_total / damage_total /
    headshot_kills_total；ADR = damage_total / 正式回合数，按计分板显示取整
    （floor，例如 135.21 → 135）。
  - 每回合的 K / A / D / 爆头 / 伤害使用 round_end tick 的总分差值，用于 T/CT 分面。
  - 已用 2026-11-22 Dust2 13-6 的计分板截图 fixture 校验：10 名选手
    K/D/A/ADR 逐项一致；报告顶部会显示校验横幅。

推断层：
  - KAST：由计分板每回合 K/A/S 推断， traded（死亡后 5 秒内队友完成击杀）
    只在事件层逐回合核对一致的回合计入；计分板截图没有此列，
    未做 fixture 校验。报告表头标“推断”。

参考层：
  - Open Kills：由计分板 kills_total 的每回合首次增量推断；标“参考”，
    不参与 fixture 精确层判定。

事件层：
  - Kill Matrix：从 player_death 逐条聚合，但每个正式回合先与计分板
    每回合 K/D 差分逐人核对：只有 10 人全部一致的回合才进入矩阵，
    不一致的回合整体排除，并在报告中列出覆盖度与排除回合号。
  - 攻击者为空的击杀（摔死/烧死/自杀等）归入单独的“环境”行/列，不丢弃。
  - 如果没有任何回合通过核对，Kill Matrix 整块隐藏并说明原因；
    Swing 改用计分板每回合 K/A/D 的回合级近似，并标“近似”，绝不冒充精确。

近似层：
  - Rating、Swing、MK rating、经济调整、MVP。
  - Rating 采用 Rating 2.0 近似公式：
        0.0073×KAST% + 0.3591×KPR − 0.5329×DPR
        + 0.2372×Impact + 0.0032×ADR + 0.1587
    Impact = 2.13×KPR + 0.42×APR − 0.41，再加首杀与多杀回合加权；
    报告附“Rating 分解”表，KPR/DPR/KAST/Impact/ADR 各项可单独与
    HLTV 交叉核对；但最终 Rating 不是 HLTV 官方数值，不能逐位比对。

T/CT 分面：
  - 主表、Performance、Kill Matrix（可用时）、雷达图、对比条都支持 All/T/CT；
  - K/D/A/ADR/KAST 的 T/CT 分面来自计分板每回合差值；All = T + CT；
  - 事件层不可靠时，依赖事件的字段会隐藏，而不是用半套数据猜。

关闭/不完整：
  - 如果 demo 文件还在写入，程序会等待文件稳定；
  - 如果 demo 本身不完整（例如中途退出服务器），报告会提示“数据可能不完整”；
    不会用猜测补齐。


五、计分板 fixture 与精确校验
----------------------------------------------------------------------
精确层以同一场的游戏内计分板截图为 fixture 逐项核对。
fixture 用 validated_fields 指定本场能核对的字段：

  - 计分板有 K/D/A/ADR：validated_fields = ["kills","deaths","assists","adr"]
  - 如果计分板还能看到 KAST：可以再加 "kast"；
  - Open Kills 是参考字段，可填可不填，不参与精确层判定。

ADR 取整方式在 fixture 里写：
    "adr_rounding": "floor", "precision": {"adr": 0}
游戏内计分板把 135.21 显示为 135，所以必须按 floor 比较，不能用四舍五入。

为什么必须核对？
  - 这个 demo 存在 bot 接管/击杀归因错位：旧的事件层会把 43 次击杀记到
    Beneqin1，而计分板是 23 次；只有计分板属性才能还原真实 K/D/A/ADR。
  - KAST 截图里没有，所以报告只能标“推断”，不能声称与计分板一致。

怎么做？
  1. 打开 fixtures/FIXTURE_FORMAT.txt，按 JSON 格式填写；
  2. 10 名选手都要填，不能只填 Beneqin1；
  3. 保存成：
         我的对局.fixture.json        （和 demo 放同一目录）
     或者
         fixtures/我的对局.fixture.json
     程序会按 demo 的 SHA-256 自动找到它；
  4. 也可以点“计分板 fixture → 浏览…”手动选择。

手动 Demo 和自动监听都默认允许“没有 fixture”：
  - 没有匹配 fixture：生成报告，但顶部标“未校验”横幅；
  - fixture 存在但不一致：停止生成，弹出差异报告；
  - 想强制要求 fixture：命令行加 --require-fixture。

开发/排查时可用命令行 --allow-unvalidated-preview 生成带红色横幅的预览，
不能当作最终数据。

fixtures/user_mirage_20261122.fixture.json 是本次用户 Mirage demo 的 fixture
（13-1，10 人 K/D/A/ADR，ADR 向下取整），校验命令：
  .venv\Scripts\python.exe validate_stats.py "D:\...\auto-20261122-1713-de_mirage-Beneqin1.dem" fixtures\user_mirage_20261122.fixture.json
结果：10 人 × 4 项 = 40 checks，verified。

fixtures/user_dust2_20261122.fixture.json 是上一场 Dust2 demo 的 fixture
（13-6，10 人 K/D/A/ADR；仅当该 demo 仍在时可用），校验命令：
  .venv\Scripts\python.exe validate_stats.py "D:\...\auto-20261122-1358-de_dust2-Beneqin1.dem" fixtures\user_dust2_20261122.fixture.json
结果：10 人 × 4 项 = 40 checks，verified。

fixtures/dev_mirage.fixture.json 是公开开发 demo 的自测 fixture，只用于
验证程序本身；它不能校验你的 demo（SHA-256 不同会被拒绝）。
当前自测结果是 10 人 × 5 项 = 50 项全部一致。


六、校验脚本 validate_stats.py
----------------------------------------------------------------------
位置：项目根部的 validate_stats.py（需要项目里的 .venv Python）。
exe 版不需要手动跑它：生成报告时程序会自动做同一套校验。
这一节是给源码/维护者排查用的。

在项目文件夹打开 PowerShell：

  .venv\Scripts\python.exe validate_stats.py "D:\路径\我的对局.dem"

显式指定 fixture：

  .venv\Scripts\python.exe validate_stats.py "D:\路径\我的对局.dem" "D:\路径\我的对局.fixture.json"

输出：
  - verified：fixture 中 validated_fields 指定的字段全部一致，退出码 0；
  - mismatch：有差异，逐项列出“计分板 vs 解析”，退出码 1；
  - partial：fixture 缺字段或无法解析，退出码 1；
  - unverified：没找到 fixture，退出码 2。
  Open Kills 若填写，会作为参考项显示，但不影响 verified 判定。
  截图没有 KAST 时，validated_fields 不写 kast，就不会被误判为缺字段。

校验脚本只读 demo，不会修改任何文件。


七、报告里有什么
----------------------------------------------------------------------
1. 顶部
   - 大号地图标题（渐变）、生成时间、两队发光比分；
   - MVP 金卡：获胜队伍中 Rating 最高的选手，基于本报告近似 Rating；
   - 校验状态横幅；绿色“已校验”表示 fixture 指定字段全部对上；
   - 解析提示横幅：逐回合核对覆盖度、事件层降级等提示会集中列在这里；
   - 不再显示平均 Rating、总回合、总击杀、队伍总击杀。

2. Match stats 主表
   - Player / K-D / +/- / K/D / K (HS) / D / A / KAST（推断）/ Open Kills（参考）/
     ADR / Swing（近似）/ Rating（近似）
   - 支持 All / T / CT 三态切换。

3. Rating 分解表
   - 每人一行：KPR / DPR / KAST / Impact / ADR / Swing / econ Δ / Rating；
   - 用于与 HLTV 官方分项交叉核对，或排查 Rating 异常来自哪一项。

4. Performance Rating 条形图
   - 每个选手一根条，长度是 Rating（近似值）；
   - 仅此图按 Rating 从高到低统一排序，不分队伍；
   - 颜色：Rating ≥ 1.05 绿色，0.96–1.04 中性深色，≤ 0.95 红色。

5. Kill matrix
   - 行 = 击杀者（Team A），列 = 被击杀者（Team B）；
   - 每格左侧数字 = 行选手击杀列选手，右侧数字 = 反向击杀，颜色越深次数越多；
   - 数据来自 demo 的 player_death 逐条击杀事件，且每个回合先与计分板
     差分核对，仅保留完全一致的回合；覆盖度与排除回合号显示在标题下方；
   - 支持“击杀视角 / 死亡视角”切换（死亡视角 = 行列转置）；
   - 支持 All / First kills / AWP / 手枪 四种范围；
   - 攻击者为空的死亡归入“环境”行/列，不丢弃；
   - 本图是跨队矩阵；队内击杀不显示；没有回合通过核对时整块隐藏并说明。

6. Player performance overview
   - 目标选手的六维雷达图：KPR / Rating / Impact / ADR / KAST / Surviving；
   - 与全场平均对比（金色 = 选手，红色虚线 = 全场平均）；
   - 七项指标对比条：KPR / DPR / KAST / MK rating / Swing / ADR / Rating；
   - 雷达轴为英文 + 小号中文；All/T/CT 联动。

7. 页面底部
   - 统一“口径说明”：K/D/A/ADR 来源、KAST 推断、事件层降级、
     Rating/Swing/MVP 近似、All/T/CT 定义；
   - 只针对当前地图，不做跨图汇总。


八、杀毒软件误报
----------------------------------------------------------------------
PyInstaller 打包的程序偶尔会被杀毒软件误报。优先使用 onedir 版，并只把
解压后的整个 CS2Stats 文件夹加入杀毒软件的“排除/信任”列表。

不要为了运行程序关闭实时防护。若仍被拦截：
  - 确认压缩包来自你信任的位置；
  - 用源码运行（见第十节）；
  - 或重新用本机环境执行 build_exe.ps1 构建。
onefile 版启动时会解压到临时目录，更容易触发启发式误报；功能与 onedir
相同，只是启动慢几秒。


九、更换 awpy 版本
----------------------------------------------------------------------
项目依赖固定为 awpy==3.3.0、demoparser2==0.42.2、pandas==3.1.1。
计分板属性来自 demoparser2；awpy 负责回合、击杀事件与 T/CT 重建。
更换版本前先备份环境：

  .venv\Scripts\python.exe -m pip freeze > requirements-freeze-backup.txt
  .venv\Scripts\python.exe -m pip install awpy==<新版本> demoparser2==<新版本> pandas==<新版本>

然后必须重新跑：

  .venv\Scripts\python.exe validate_stats.py "_vendor\demos\dev_mirage.dem"

如果 awpy 或 demoparser2 改了计分板属性名/ADR 口径，校验会失败；不要直接发布新 exe。
本项目的计分板属性名、floor 取整与 T/CT 差值逻辑集中在
cs2stats/scoreboard.py；换版本需要同步调整并重跑两份 fixture。


十、从源码运行 / 重新打包
----------------------------------------------------------------------
需要 Python 3.12 和项目 .venv。

启动 GUI：
  .venv\Scripts\python.exe run_app.py

自动监听（无界面）：
  .venv\Scripts\python.exe run_app.py --watch "D:\...\game\csgo\replays" --watch-out "D:\报告"

只处理最新一个 demo 后退出（测试）：
  .venv\Scripts\python.exe run_app.py --watch "D:\...\game\csgo" --watch-once --no-open

手动解析：
  .venv\Scripts\python.exe run_app.py "D:\...\我的对局.dem" ^
      --fixture "D:\...\我的对局.fixture.json"

解析缓存：
  demo 解析结果按文件 SHA-256 缓存在程序根目录 cache\，同一 demo 第二次
  解析只需零点几秒；demo 文件变了会自动重新解析。
  .venv\Scripts\python.exe run_app.py "D:\...\我的对局.dem" --no-cache   （跳过缓存）
  .venv\Scripts\python.exe run_app.py --clear-cache                       （清空缓存）

打包（默认 onedir）：
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1

打包 onefile：
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\build_exe.ps1 -OneFile

输出在 dist\，默认同时生成 zip。构建脚本会把 README.txt、CHANGELOG.txt、
validate_stats.py 和必要的 fixture 说明复制进包；开发期的 GSI 实测记录
不再放入发布包。


十一、文件说明
----------------------------------------------------------------------
CS2Stats.exe                 主程序（onedir 版在 CS2Stats 文件夹内）
output/                      报告输出目录（首次生成报告时自动创建）
cache/                       解析结果缓存（首次解析后自动创建，可整目录删除）
stats.cfg                    首次运行生成的 GOTV 命令（两行）
settings.json                首次运行标记、手动选择的目录
run_app.py                   源码入口
validate_stats.py            独立校验脚本
README.txt                   本文件
CHANGELOG.txt                本版改动清单
fixtures/                    计分板 fixture 示例与格式说明（可选）
fixtures/user_dust2_20261122.fixture.json  本次 Dust2 用户对局 fixture
fixtures/user_mirage_20261122.fixture.json 本次 Mirage 用户对局 fixture
fixtures/FIXTURE_FORMAT.txt  fixture 怎么写
build_exe.ps1                PyInstaller 构建脚本
requirements.txt             运行依赖
requirements-build.txt       打包依赖
cs2stats/                    源码包
  gotv_config.py             生成/安装兼容 GOTV cfg，定位 replays 目录
  first_run.py               首次运行生成/复制 stats.cfg，清理旧版 cfg
  paths.py                   exe 同级 output/settings/stats.cfg 路径
  demo_watch.py              监听 demo 文件夹、稳定等待、去重
  scoreboard.py              从 demoparser2 计分板属性重写精确层；事件层逐回合核对
  demo_parser.py             demo 解析与 T/CT 重建
  validation.py              fixture 校验
  report_data.py             报告数据
  rating.py                  Rating 2.0 近似 / Swing
  cache.py                   解析结果缓存（sha256 + 版本守卫）
  render.py                  HTML 渲染
  app.py / gui.py / cli.py   流程、界面、命令行
  templates/                 Jinja2 模板
  static/echarts.min.js      离线图表库


十二、边界与承诺
----------------------------------------------------------------------
  - 精确层（K/D/A、ADR、K(HS)）来自计分板属性，K/D/A/ADR 用 fixture 校验；
  - KAST 由计分板每回合 K/A/S 推断，traded 只计入逐回合核对一致的回合；
    截图无此列，未校验；
  - Open Kills 由计分板 kills_total 每回合首次增量推断，标“参考”；
  - Kill Matrix 依赖事件层，逐回合与计分板差分核对，仅保留一致回合；
    无一致回合时隐藏；Swing 改用计分板每回合 K/A/D 近似；
  - Rating（Rating 2.0 近似公式）/ Swing / MK rating / 经济调整 / MVP
    是近似值，不是 HLTV 官方算法；
  - 自动监听只读本地 demo 文件夹，不上传任何数据；
  - 不伪造队友数据：demo 里有几个人就显示几个人；
  - 文件未稳定不解析；损坏或不完整的 demo 会给出错误/缺口提示，不猜测补齐；
  - 单张地图报告，不做跨图汇总。
