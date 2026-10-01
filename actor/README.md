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
HOME 是本机路径、默认在仓库外、不进 git（本机：`D:\DSH\dsh-actor`，记录在同目录的 `home.txt`，可用
`-HomePath` 或环境变量 `ACTOR_HOME` 改）。

```
actor/                     ← 代码（可提交、可搬走）
  actor.py            常驻守护进程（抓屏 / 手 / UIA / 视觉 / op 表 / TCP :8731）
  act.py              极简客户端（stdlib）：act.py ping | run skills/x.json | '{...}'
  act.cmd             命令行薄封装
  actor.ps1           Windows 侧助手：-Setup -Start -Stop -Status -Bench -Send -File -Tail -Where
  skills/demo_calc.py 验证技能：纯结构通道驱动计算器并读回结果
  tests/probe_uia.py  裸 UIA 探针（排查 UIA 行为时用）
  tests/dirty_calc.py 把计算器弄成「脏状态」并留着不关，用来验证 demo 的确定性
  .gitignore          把 home.txt / 日志 / png 挡在 git 外

D:\DSH\dsh-actor\          ← HOME（机器本地，不提交）
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
解释器用 DSH 自带的 python（3.12 + numpy），`pip install --target` 装依赖，**不往 C 盘写任何东西**。

## 协议（一行 JSON 进，一行 JSON 出）

| op | 说明 |
|---|---|
| `ping` | pid / uptime / 屏幕几何 / DPI / python 版本 |
| `shot` `save` | 抓帧（`path`、`region`），带 `frame_id` 缓存 |
| `find` | `image`/`template`(+`tmpl_rect`) 模板匹配；`color`+`tol`+`min_area` 连通域；`uia`+`selector` 控件查找 |
| `click` `move` `drag` | 目标可以是 `xy`、`uia` 选择器（点元素中心）、`color`/`template` 命中 |
| `type` `key` `scroll` | 文本、键名（enter/esc/tab/ctrl+z…）、滚轮 |
| `uia` | `windows` / `find` / `tree` / `point` / `invoke` / `focus` / `setvalue` |
| `window` | 窗口管理：`front`（抢前台并激活）、`top`/`untop`（置顶钉住，做动作期间不让别的程序抢走焦点）、`info`、`max`/`min`/`restore`/`move`/`close`；`hwnd=` 或 `title_contains=` |
| `wait_for` | 轮询条件（元素出现、颜色出现、模板出现），带超时 |
| `watch` | 后台按 fps 抓帧做差分（等画面变化，不用轮询） |
| `run` | **一次调用跑完一整段技能**：`{"op":"run","steps":[{...},{...}]}`，逐步返回 `ms/ok` |
| `bench` `log` `stop` | 基准、日志、退出 |

`run` 是省往返的关键：一个技能 = 一次调用，返回逐步 trace。

## 实测（2560×1600，本机）

| 动作 | 旧的「一次工具往返」 | 现在 |
|---|---|---|
| 全屏抓帧 | 1 次往返 + 落盘 | **46 ms** |
| 列全部顶层窗口（名字/类/hwnd/矩形） | 2–4 s | **48 ms** |
| 按 aid/name/类型找控件（36 个按钮） | 1–3 s | **59 ms** |
| 点击（含人手缓动 100 ms） | 1–2 s | **~120 ms** |
| 输入 `7*8` | 2–4 s | **50 ms** |
| 按 Enter | 1–2 s | **54 ms** |
| 整屏颜色连通域 | 1.1 s | **102 ms** |
| 整屏模板匹配（60×60） | 573 ms | **83 ms** |
| **整段技能：启动计算器 → `7*8=` → 读回 56 → `12+30` → 按名字点「等于」→ 读回 42** | — | **0.62–0.72 s（含启动 App），像素读取 0 次** |

三个配套改动让它确定：`window front` 抢前台（SendInput 只会发给有焦点的窗口）→ `key esc` 清空残留输入 → `wait_display` 轮询结构通道而不是盲等 → 结尾 `window close` 让下次从干净状态开始。`tests/dirty_calc.py` 专门把计算器弄成脏状态（残留显示 42）留着不关，用来证明 demo 从脏状态照样 PASS。

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

## 与 dsh-vision-kit 的关系

`actor/` 是「手和眼睛的常驻回路」；`dsh-vision-kit` 里的抓帧/OCR/CV 工具是它的视觉资产来源。
两者共用同一个自带 python（`dsh-runtimes`），`comtypes` 装在 HOME 的 `pylibs`（本机 `D:\DSH\dsh-actor\pylibs`）。

## 为什么状态不放在仓库目录

1. 代码要能原样复制到任何机器 / 提交到 git，而依赖、日志、抓帧都是机器本地的。
2. 这台机器的 C 盘紧张：约定「**代码与状态都不往 C 盘写**」，pip 缓存、TEMP、日志全部落在 HOME（D 盘），
   连 `TEMP`/`TMP` 都被重定向到 `HOME\tmp`，防止长跑时把 C 盘塞满。
