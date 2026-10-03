# DSH PC Actor — 像人一样操作 PC 的常驻回路

> 起因：一次「用视觉读完蜘蛛纸牌并打通」的任务里，我做了 200+ 次工具往返，每次都要新起一个
> PowerShell、冷启动一次 Python、截一次图、看一次图。慢的不是识别，是**架构**：我把
> 「眼睛一直开着、手一直放着、找到就点、点完就看」的人体回路，拆成了**请求—响应**循环。
>
> 人的做法是：**常驻的眼睛 + 常驻的手 + 肌肉记忆**。这个 actor 就是那个回路。

## 三层通道（按优先级）

| 通道 | 用什么 | 什么时候用 | 实测 |
|---|---|---|---|
| **结构** | UI Automation（进程内 COM，单 STA 线程） | 只要控件在 UIA 树里（几乎所有 Win32/WPF/UWP 控件、浏览器可访问节点） | 1–60 ms |
| **动作** | SendInput（真实鼠标/键盘事件） | 一切点击/输入；人手缓动可关 | 0.05–0.12 s |
| **视觉** | 常驻抓帧 + numpy 连通域 / 金字塔模板匹配 | UIA 给不了的地方：游戏画布、自绘 UI、canvas | 45 + 83–102 ms |

**规则**：先问「有没有结构通道」，再问「有没有日志/存档/深链」，最后才上像素。

## 文件：代码在仓库里，状态在 HOME 里

这层目录**只有代码**；所有会变的东西（python 依赖、日志、抓帧、port.txt、临时文件、pip 缓存）都在 **HOME**，
HOME 是本机路径、默认在仓库外、不进 git（这台机器上是 `D:\DSH\dsh-actor`，**只是例子**：实际位置记在同目录的
`home.txt`，`-Where` 会打印，可用 `-HomePath` 或环境变量 `ACTOR_HOME` 改）。

```
actor/                     ← 代码（可提交、可搬走）
  actor.py            常驻守护进程（抓屏 / 手 / UIA / 视觉 / op 表 / TCP :8731）
  act.py              极简客户端（stdlib）：act.py ping | run skills/x.json | '{...}'
  act.cmd             命令行薄封装
  actor.ps1           Windows 侧助手：-Setup -Start -Stop -Status -Bench -Send -File -Tail -Where
  skills/demo_calc.py 验证技能：纯结构通道驱动计算器并读回结果
  tests/probe_uia.py  裸 UIA 探针（排查 UIA 行为时用）
  tests/dirty_calc.py 把计算器弄成「脏状态」并留着不关，用来验证 demo 的确定性
  tests/bench_fast.py 快循环基准：state / probe / run / front / 批量输入 逐项耗时
  tests/verify_type.py 证明批量输入照样落字：150、500 字符 + 像素 diff + ASCII 墨迹图
  .gitignore          把 home.txt / 日志 / png 挡在 git 外

D:\DSH\dsh-actor\          ← HOME（机器本地，不提交；`-Where` 打印实际路径，这里只是这台机器的例子）
  pylibs\             pip 装到这里的依赖（comtypes）
  logs\               actor.log + stdout/stderr
  port.txt            守护进程端口
  tmp\  cache\pip\    TEMP 与 pip 缓存都指到这里，C 盘不再长
```

## 快速开始

```bat
actor\actor.ps1 -Setup          :: 配齐：建 HOME、把依赖 pip 装到 HOME\pylibs（幂等；-Start 会自动补）
actor\actor.ps1 -Where          :: 看代码在哪、HOME 在哪、依赖齐不齐
actor\actor.ps1 -Start          :: 起守护进程（幂等；客户端也会自动拉起）
actor\actor.ps1 -Bench          :: 实测各通道延迟
actor\act.cmd ping
actor\act.cmd "{""op"":""uia"",""what"":""windows"",""max"":10}"
python actor\skills\demo_calc.py
```

客户端在守护进程没起来时会**自动拉起**它（并带上 HOME 与 `HOME\pylibs`），所以技能脚本可以直接跑。
解释器按 `-Python` / `%ACTOR_PY%` → DSH 自带的 python（3.12 + numpy）→ PATH 上的 `python` / `py` 依次找，
`pip install --target` 装依赖，**不往 C 盘写任何东西**。

## 协议（一行 JSON 进，一行 JSON 出）

| op | 说明 |
|---|---|
| `ping` | pid / uptime / 屏幕几何 / DPI / python 版本 |
| `shot` `save` | 抓帧（`path`、`region`），带 `frame_id` 缓存（只要像素结论时**优先 `probe`**，别拉 PNG） |
| `state` | **一次调用 = 整张桌面摘要**：前台窗口 + 顶层窗口表（名字截断 70 字符、矩形），`uia:true` 再加该窗口的**具名元素平表**（`scanned`/`n`/`items`，默认上限 60）——替代几百 KB 的 `tree` dump |
| `probe` | **在 actor 里做像素账、只回文本**（不落盘、不传图）：`colors` 主色 + 每色 bbox、`grid` 每格平均色、`ink` ASCII 墨迹图、`mark` 存基线、`diff` 与基线比（`changed_px`/`pct`/`bbox`；**基线不覆盖**，可反复 diff） |
| `find` | `image`/`template`(+`tmpl_rect`) 模板匹配；`color`+`tol`+`min_area` 连通域；`uia`+`selector` 控件查找 |
| `click` `move` `drag` | 目标可以是 `xy`、`uia` 选择器（点元素中心）、`color`/`template` 命中 |
| `type` `key` `scroll` | 文本、键名（enter/esc/tab/ctrl+z…）、滚轮 |
| `uia` | `windows` / `find` / `tree` / `point` / `invoke` / `focus` / `setvalue` |
| `window` | 窗口管理：`front`（抢前台并激活）、`top`/`untop`（置顶钉住，做动作期间不让别的程序抢走焦点）、`info`、`max`/`min`/`restore`/`move`/`close`；`hwnd=` 或 `title_contains=` |
| `launch` | **启动程序并等它的窗口**：`path`（`.exe` / `.lnk` / `.bat` / `shell:appsFolder\<AUMID>`）、`args`、`cwd`、`wait`（窗口名片段，阻塞到窗口出现）、`wait_pid`、`timeout_ms`（默认 20 s）。`wait` 命中的窗口已经开着就**复用、不重复启动**（`force: true` 才另起一个实例）；宏自带 `launch` 时，回放会先把应用拉起来 |
| `wait_for` | 轮询条件（元素出现、颜色出现、模板出现），带超时 |
| `watch` | 后台按 fps 抓帧做差分（等画面变化，不用轮询） |
| `run` | **一次调用跑完一整段技能**：`{"op":"run","steps":[{...},{...}]}`，逐步返回 `ms/ok`；加 `"results": true` 则每步自己那份返回也塞进该步的 `data`（长文本截断、长列表保留 6 项 + `…(N more items)`，`frame`/`png` 不带） |
| `macro` | **录制与回放**：`what=run` 一次调用重放一条宏（步骤在服务端跑完，逐步 `ms` 照旧回来）；`what=list`/`get`/`del`/`save` 管理；`list`/`run` 这类裸词可以直接写成 `act.cmd macro list` |
| `bench` `log` `stop` | 基准、日志、退出 |

`run` 是省往返的关键：一个技能 = 一次调用，返回逐步 trace。读数据的 op（`uia`/`state`/`probe`/`find`）默认**只回报标量**，要它们的结果就传 `results`——否则得把那个 op 单独再发一次（这就是"一次假设一次往返"的旧毛病）。
客户端两种写法都认：`act.cmd run <文件>` 与 `act.cmd <文件>`（后者是 README 里一直写着、代码以前不认的那个）。

任何输入 op（`click`/`move`/`type`/`key`/`drag`）都能带 `front=<hwnd>` 或 `front_title=<子串>`：先抢前台（默认顺手置顶钉住）再动作，并把 `front` 的结果带回来。`click` 默认**不再做 100 ms 人手缓动**（要旧行为传 `ease:true`）；`type` 传 `per_char_ms:0` 就是**整串一次性 SendInput**（遇到丢字的 app 用 `chunk:40` 分批）；`uia` 的 `what:"tree"` 加 `compact:true` 直接给平表而不是整棵树。

## 实测（2560×1600，本机）

| 动作 | 旧的「一次工具往返」 | 现在 |
|---|---|---|
| 全屏抓帧 | 1 次往返 + 落盘 | **46 ms** |
| 列全部顶层窗口（名字/类/hwnd/矩形） | 2–4 s | **48 ms** |
| 按 aid/name/类型找控件（36 个按钮） | 1–3 s | **59 ms** |
| 点击（自动抢前台，无人手缓动） | 1–2 s | **33–46 ms**（旧的缓动版 120 ms） |
| 桌面摘要（前台 + 顶层窗口表） | 2–4 s | **45–92 ms** |
| 具名元素平表（扫 109 节点 → 41 个具名） | 数秒 + 300 KB | **155–196 ms，几百字节** |
| 像素结论：主色+bbox / 全屏 diff / 墨迹图 | 1 次往返 + 落盘 + PIL 载入 ≈ 1 s | **63–194 ms**（600×400 主色 63、全屏 194、diff 129、墨迹 86） |
| 输入 150 字符 | 2–4 s | **72 ms**（逐字 12 ms 的旧路径 1 956 ms） |
| 四步交互（mark → 输 150 字 → 等 0.4 s → diff）一次调用 | 4 次往返 4–12 s | **622 ms** |
| 输入 `7*8` | 2–4 s | **50 ms** |
| 按 Enter | 1–2 s | **54 ms** |
| 整屏颜色连通域 | 1.1 s | **102 ms** |
| 整屏模板匹配（60×60） | 573 ms | **83 ms** |
| **整段技能：启动计算器 → `7*8=` → 读回 56 → `12+30` → 按名字点「等于」→ 读回 42** | — | **0.62–0.72 s（含启动 App），像素读取 0 次** |

三个配套改动让它确定：`window front` 抢前台（SendInput 只会发给有焦点的窗口）→ `key esc` 清空残留输入 → `wait_display` 轮询结构通道而不是盲等 → 结尾 `window close` 让下次从干净状态开始。`tests/dirty_calc.py` 专门把计算器弄成脏状态（残留显示 42）留着不关，用来证明 demo 从脏状态照样 PASS。

## 快循环：一次调用跑完「看 → 动 → 验」

```json
{"op":"run","steps":[
  {"op":"state","uia":true,"title_contains":"Notepad"},
  {"op":"probe","mode":"mark","region":[1606,299,2313,1406]},
  {"op":"type","text":"hello","per_char_ms":0,"front":1443004},
  {"op":"sleep","ms":300},
  {"op":"probe","mode":"diff","region":[1606,299,2313,1406]}
]}
```

一个 `run` = 一次往返；`probe` 的结论是几行文本，而不是几百 KB 的 PNG；`front=` 保证 SendInput 落在目标窗口上。
同样的「读状态 → 输入 150 字符 → 校验」，从 4 次调用 ≈ 6 s 降到 **622 ms**（`tests/verify_type.py` 里有 A/B 对照与墨迹图）。

慢的从来不是鼠标（一次点击 33–46 ms），而是**编排**：每步新起 pwsh + 冷启 python + 截一张图 + 盲等。

### 录制一次，之后回放

第二次做同一条流程，就别再让模型走第二遍：跑成功的 `run` 可以自己留下。

```json
{"op":"run","record":"calc-eval","results":true,"front_title":"计算器",
 "args":{"expr":"7*8"},
 "steps":[
   {"op":"window","mode":"front","title_contains":"计算器"},
   {"op":"click","target":{"uia":{"selector":{"aid":"CalculatorResults"}}}},
   {"op":"key","key":"esc"},
   {"op":"type","text":"{{expr}}"},
   {"op":"key","key":"enter"},
   {"op":"uia","what":"find","selector":{"aid":"CalculatorResults"},"as":"disp"},
   {"op":"window","mode":"untop","title_contains":"计算器"}
 ]}
```

```powershell
.\act.cmd run D:\DSH\dsh-actor\tmp\calc.json          # 跑完且每步 ok → 存成 $ACTOR_HOME\macros\calc-eval.json
.\act.cmd macro list                                  # 名字 / 步数 / 回放次数 / 上次耗时 / FAILED
.\act.cmd '{"op":"macro","what":"run","name":"calc-eval","args":{"expr":"9*9"},"results":true}'
.\act.cmd macro what=del name=calc-eval               # 也支持 save（from: last|<run_id>）
```

- 宏是普通 JSON：`steps` + `args`（默认参数）+ `front` + `note` + `stats`，可读可改可进 git。
- `{{expr}}` 取 `args`，也取前面某步 `as: "disp"` 存下的返回（`{{disp.hits.0.name}}`）；
  **整串就是一个占位符时直接返回原值**，所以捕获到的 `rect`/`bounds` 能当坐标用。`"literal": true` 整步不展开。
- 回放失败会补 `failed_step` 和 `hint`；`dry: true` 只展开不执行，用来检查参数。
- 实测（上面这 7 步，Calculator）：录制 1.8 s，回放 **0.30 s**（服务端 0.20 + 客户端 0.10），三次分别得 56 / 579 / 81。
  等价的手工路线是 7 次往返 × (模型延迟 + 每步 1.2–2.6 s 的 UIA 扫描)。
- 回放回复里的 `front_resolve_ms`（客户端表头写成 `front=`）是**第 0 步之前**解析前台窗口的耗时：
  守护进程刚起或目标程序刚启动、窗口还没缓存时实测 **1361 ms**（那次 wall ≈1.7 s），缓存之后 1–3 ms。
  `total_ms` 不含它，`wall − total_ms` 的差额基本等于它加客户端启动（约 95 ms）。

回放快不是因为它"记住了像素"，而是因为这次不再重复两次查找：**窗口按标题只解析一次**
（`title_contains` 命中后缓存，重解析只要两次 Win32 调用：1371 ms → **3 ms**，窗口关掉或走 `window mode=close` 才失效），
**UIA 选择器先在当前窗口内找**（95 ms）再退回全桌面（1500 ms）——`run`/`macro run` 解析一次前台窗口后
用 `scope_hwnd` 发给每一步，`click`/`move`/`drag` 的 target 与 `uia`/`find` 都认这个字段。
所以录进宏的选择器**不带 hwnd 也能跨重启**，同时保持快。

宏可以自己启动程序：把 `{"op":"launch","path":"…","wait":"窗口名片段"}` 写成第一步，它会随宏一起存下来
（宏文件里多一个 `launch` 字段），回放时窗口不在就**先启动再跑**。`wait` 命中的窗口已经开着时**不重复启动**
（`force: true` 才另起实例），所以同一条宏在「应用开着」和「应用没开」两种情况下都能原样回放。

实测（上面那 8 步 = 7 步前面加一个冷启动 `launch`，Calculator）：应用已关时回放 wall 5.3 s =
**等窗口 2.6 s** + 回放 **0.22 s**，读回「显示为 42」；应用已开时那一步 0.2 ms、`start=reused`。
等窗口的时间标在客户端表头 `start=`，和 `front=` 一样**不在 `total_ms` 里**。`launch` 拉起来的窗口
还会成为后续步骤的 UIA 搜索范围：`click` 520 → 58 ms，读回显 1342 → 19 ms。

## 写一个技能

一次性：

```json
{"op":"run","steps":[
  {"op":"uia","what":"find","selector":{"aid":"CalculatorResults"}},
  {"op":"click","target":{"uia":{"selector":{"aid":"CalculatorResults"}}}},
  {"op":"type","text":"7*8"},
  {"op":"key","key":"enter"},
  {"op":"uia","what":"find","selector":{"aid":"CalculatorResults"}}
]}
```

可复用：写成 `skills/*.py`，`from act import send_or_start`，把「等待 → 操作 → 断言」写全，
一次运行给出 trace 和结论（`skills/demo_calc.py` 就是模板）。

## 踩过的坑（都是真的）

1. **`TreeScope_Descendants = 4`，不是 1**（1 = Element）。写错的话 `FindAll` 永远返回 0，
   而 walker 遍历却正常——非常容易误判成「这个 App 不暴露 UIA」。
2. **UIA 必须单线程 + STA**：一个常驻 STA 线程独占所有 COM 对象，调用方通过队列过去（每个调用
   带超时；目标卡死就丢弃该线程换新的），否则会出现 `RPC_E_CHANGED_MODE (-2147417850)`，
   或者目标程序一卡就把 actor 拖死。
3. **UWP 的 `ApplicationFrameWindow`**：内容和标题栏都在 frame 的 UIA 子树里（`Windows.UI.Core.CoreWindow`
   是子节点），但它是**子窗口**，不在桌面顶层窗口列表里 → 必须用 frame 的 hwnd 去 `ElementFromHandle`
   再向下找。按桌面根做全树 `FindAll` 又慢又容易漏。
4. **跨线程传 COM 指针要用真对象**：代理对象直接当参数传会被 comtypes 拒绝，必须先解回真实指针。
5. **中文控制台**：`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8` + `[Console]::OutputEncoding`，
   否则拿到的是乱码，看控件名会误判。
6. **模板要有纹理**：纯色模板会在一堆位置拿满分（score 0），返回的位置没有意义。
7. **视觉通道要向量化**：连通域别写成 O(R²) 的 run 两两比较，模板匹配别用 Python 双层循环扫全屏
   —— 降采样金字塔 + `sliding_window_view` 是 7–11 倍。
8. **App 的状态会跨运行保留**：计算器会记住上次用的模式，而「绘图」模式里**根本没有**
   `CalculatorResults` —— demo 就这么在「应用没坏、脚本没坏」的情况下失败了。技能的第一步必须是
   **把 app 恢复到它期望的状态**（`ensure_standard()` 走导航面板点回「标准」）。顺带一坑：面板项是
   `ListItem`，名字是「标准 计算器」「绘图 计算器」这种带后缀的串，**必须按子串匹配**，精确匹配找不到。
9. **`type` 快不等于 app 收得下**：一次性 SendInput 批次在记事本、计算器上都稳（150 字 72 ms、500 字 332 ms），
   但遇到丢字的 app 要退到 `chunk:40` 分批 —— 快路径必须是「可退化的」，不是「二选一」。
10. **别从 PowerShell 把 JSON 递给 `act.cmd`**：`act.cmd '{"op":"uia","what":"windows"}'` 里的内层双引号
    会在 shell 那一层被吃掉，`act.py` 收到 `{op:uia,...}`，报
    `Expecting property name enclosed in double quotes: line 1 column 2` —— 看起来像 actor 挂了，
    其实请求根本没成型。要么直接调 `act.py`（`PYTHONPATH=<HOME>\pylibs`，解释器用 `-Python` 那套候选），
    要么把请求写进文件再传路径。
11. **网页/表单自动化：别粘两次，也别信路径**：Web 编辑器加载完通常**自带焦点**（GitHub 新建文件页就是），
    一次 `ctrl+v` 已经进去了，再「点一下编辑器再粘」就是两份 —— 实测粘成 80 行，还把 `jobs:` 和它的
    下一行粘在了一起。粘完先看编辑器右下角的行数。另外 GitHub 新建文件的地址要用
    `new/<branch>?filename=<完整路径>`，写成 `new/<branch>/<路径>` 会把路径当成目录，最后落在
    `.github/workflows/ci.yml/ci.yml`；提交后拿 API 核对 blob sha，才算真凭据。

## 与 dsh-vision-kit 的关系

`actor/` 是「手和眼睛的常驻回路」；`dsh-vision-kit` 里的抓帧/OCR/CV 工具是它的视觉资产来源。
两者共用同一个解释器（默认取 DSH 自带的 python，见上），`comtypes` 装在 HOME 的 `pylibs`（例如 `D:\DSH\dsh-actor\pylibs`）。

## 为什么状态不放在仓库目录

1. 代码要能原样复制到任何机器 / 提交到 git，而依赖、日志、抓帧都是机器本地的。
2. 这台机器的 C 盘紧张：约定「**代码与状态都不往 C 盘写**」，pip 缓存、TEMP、日志全部落在 HOME（D 盘），
   连 `TEMP`/`TMP` 都被重定向到 `HOME\tmp`，防止长跑时把 C 盘塞满。
