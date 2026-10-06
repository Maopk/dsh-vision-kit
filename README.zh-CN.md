# dsh-vision-kit

给 **DeepSeek Harness** 桌面版（Windows）用的工具包：让 AI 真的**看见屏幕，并且能动手**。仓库里同时住着两代东西：

| 代 | 是什么 | 状态 |
|---|---|---|
| **`actor/` —— PC Actor** | 常驻守护进程：**UIA 结构**取控件、`SendInput` 当手、像素方法兜底，**一次 `run` 调用跑完一整段技能**。每步 40–140 ms。 | **现在的主路径** |
| `plugins/`、`tools/`、`docs/`、`examples/`、`tests/` | 最初那套**零模型视觉工具链**——原生截屏插件、模板匹配、几何检测、OCR、VLM 打分——以及支撑所有数字的实测报告。 | 参考资料；**OCR / 几何 / VLM 打分仍是唯一路径** |

> 这份仓库里所有的能力声明都有真值打分，不是"感觉能用"。

- English: [README.md](README.md) · Actor 手册： [actor/README.md](actor/README.md) · 结论报告： [视觉能力实测报告](docs/视觉能力实测报告.md) · [English report](docs/vision-capability-report.md)

## 为什么有这个仓库

GUI 自动化慢在一个很具体的地方：不是识别，而是**那个循环**。一次假设换一次工具往返，而每次往返
都要新起一个 shell、冷启动一次 Python——一条七步的流程就变成几分钟的等待，模型的上下文还得花在
进程管理上，而不是屏幕上。

这里的答案是**常驻 actor**：一个进程握住屏幕、输入队列和自动化会话，于是一整段技能就是**一次
调用**——而且每一步都带回自己的耗时，所以"快"是量出来的，不是猜的。

**谁需要它**：任何要自动化"没有 API 的 Windows 桌面"的人——GUI 测试、要反复做的录入或发消息
流程、需要真的去**动手**而不是只描述的 agent。**前提**：一台有交互式桌面的 Windows（actor 驱动的
是真实的前台窗口，不是无头会话）。

**从哪开始**：[`actor/`](actor/README.md) —— 一行 JSON 进、一行 JSON 出。下面的第一代与一代半
作为**参考**保留（OCR / 几何 / VLM 打分仍然只有它们能走），用 actor 不需要它们里的任何东西。

## 现在的主路径：PC Actor

`actor/` 是一个长驻进程，把屏幕、输入队列和自动化会话都握在手里，在 `127.0.0.1:8731` 上
一行 JSON 进、一行 JSON 出。

```powershell
# 不需要先装什么：客户端会自动把守护进程拉起来
.\actor\act.cmd ping
.\actor\act.cmd '{"op":"uia","mode":"windows"}'
.\actor\act.cmd '{"op":"run","steps":[ ... ]}'      # 一次调用跑完整段技能，逐步回 ms/ok
.\actor\actor.ps1 -Setup                            # 把依赖装进 actor 的 HOME
.\actor\actor.ps1 -Where                            # 代码目录 / HOME / 依赖
```

通道顺序是刻意的：**先结构**（`uia`）→ **再手**（`click`、`type`、`key`、`move`、`drag`、
`scroll`、`window`）→ **最后才是像素**（`shot`、`save`、`find`：颜色连通域 + 向量化 ZNCC
金字塔），只用在 UIA 够不到的画布和自绘界面上。

| 一步操作 | 旧方式（每次一次工具往返） | Actor |
|---|---|---|
| 列出全部顶层窗口（名字/类/hwnd/矩形） | 2–4 s | **48 ms** |
| 按名字/自动化 ID 找控件 | 1–3 s | **59 ms**（一次调用拿 36 个按钮） |
| 点击（默认不做人手缓动） | 1–2 s | **33–46 ms**（旧的缓动路径：120 ms） |
| 输入文本 / 回车 | 2–4 s / 1–2 s | **50 ms / 54 ms** |
| 全屏颜色连通域 | 1.1 s | **102 ms** |
| 全屏 60×60 模板匹配 | 573 ms | **83 ms** |
| 协议往返 / 客户端启动 | — | **12 ms / 107 ms** |
| 完整技能：启动计算器 → `7*8` → 读 56 → `12+30` → 点「等于」→ 读 42 | 几分钟、几十次调用 | **0.62–0.72 s**（热）/ **1.4 s**（含 UWP 冷启动），**读像素 0 次** |
| 回放录下来的技能——同样那 7 步，`macro` | 几分钟、几十次调用 | **0.30 s**，一次调用（服务端 0.20 s + 客户端启动 0.10 s） |
| 回放一个会自己开 App 的宏——第一步是 `launch` | 先手动打开 App，再回放 | 从关着的桌面上**一次**调用：等窗口 2.6 s + 8 步 0.22 s |
| 用 `capture start` … 亲手演示一遍 … `capture stop` 录下来 | 没有捷径：自己点一遍，就一遍 | **4 次裸点击 → 4 个 UIA 选择器**（演示 0.23 s），回放 **0.30 s** |

跑完一次 ok 的 `run` 可以把自己留下来：`"record": "<名字>"` 把它写进 `$HOME\macros\<名字>.json`，
`{"op":"macro","what":"run","name":"<名字>"}` 就能用**一次**调用回放它，逐步 trace 与原来一样。
步骤是模板——`{{arg}}` 的值来自宏里存的默认值，或回放时传入的覆盖值；`as: "x"` 把某一步的回复
接住给后面的步骤用（`{{disp.hits.0.name}}`）；如果字符串恰好只有一个占位符，它就返回那个值本身，
所以接住的矩形可以直接当目标。上面那 7 步录下来花了 1.8 s，三次回放分别得 56 / 579 / 81，每次
0.30 s。在它们前面放一条 `{"op":"launch","path":"shell:appsFolder\\<AUMID>","wait":"计算器"}`，
宏就同时记下了**怎么启动自己的 App**：从关着的桌面回放，等窗口花 2.6 s（记在 header 的 `start=`
里，不算进 `total_ms`），8 步本身 0.22 s；应用已经开着时那一步直接跳过（`start=reused`），不会
再起第二个实例。回放的开销大头不是动作，而是两次不再重复的查找：按标题解析到的窗口会被记住
（**1371 ms → 3 ms**），UIA 选择器先在当前窗口内找、找不到才退回全桌面（**95 ms vs 1500 ms**）
——所以录下来的选择器换个 App 重启也照样便携，而且解析得还快。

还没有宏、但这段流程会反复跑的时候，干脆别让模型去驱动它：指定窗口，自己动手演示一遍，让钩子去
写宏——`act.cmd capture start name=calc-demo front_title=计算器` … `act.cmd capture stop`。每次
点击都在*发生的那一刻*被反解成 UIA 选择器（先用 `aid`，没有就用 `name`，并且要确认元素仍然包含
这个点击位置）；没有元素对得上时，裁一块以点击点为中心的图像锚；只有在实在没办法时才落成裸坐标
——所以一次全靠坐标的演示，出来是结构化的：`7 + 3 =` 的四个点击变成了 `num7Button` /
`plusButton` / `num3Button` / `equalButton`。打字、组合键、滚轮和拖拽同样被记录；凡是落在被监视
窗口之外的动作都会被丢弃并计数（`dropped=`）；结果就是一个普通宏：可以参数化、可以回放，也可以
给它一条 `launch`。

细节、op 表、状态目录与十一条踩坑（`TreeScope_Descendants = 4`、
UIA 必须单 STA 线程独占 COM 且每次调用带超时、UWP 要用 `shell:appsFolder` 启动、
`SendInput` 只发给有焦点的窗口 → 先抢前台并钉住、等待要轮询结构通道而不是 sleep、
别从 PowerShell 里把 JSON 递给 `act.cmd` —— 完整清单见 `actor/README.md`）：
[actor/README.md](actor/README.md)。状态目录（HOME）是**机器本地**的：`-Where` 打印它的真实
位置，`-HomePath` / `ACTOR_HOME` 可以把它搬走，生成物一律不落进仓库。

## 技能：模型被告知的方法

`actor/` 给模型一双手，`skills/` 给它方法。一个技能 = 一个目录 + 一份 `SKILL.md`（头部
`name` / `description`，下面是 Markdown 正文）—— 这正是 DSH 会从
`$DSH_HOME\skills\<名字>\SKILL.md` 加载的格式。仓库是这份内容的唯一真相源，技能目录只是它的镜像：

```powershell
.\tools\install-skills.ps1 -Check      # N 一致 · M 需同步 · K 多余；不同步则 exit 1
.\tools\install-skills.ps1             # 默认做符号链接（跟着仓库改），不允许链接时退回复制
.\tools\check-skill-ops.py -v          # 技能里出现的每个 op 必须在 actor/actor.py 里存在
```

第一份是 `drive-a-windows-gui`，刻意写成能搬走的：正文只有方法（先判断要不要碰像素；结构优先于截图；
用 `wait_for` 而不是 `sleep`；每个动作后验一个信号；一段流程一次 `run` 跑完；失败要响；不可逆操作交回
用户）和每条规则背后的实测数字；一次性事实放附录，旁边写上"怎么重新发现它"的命令。
`check-skill-ops.py` 是 `tools/ci-static.ps1` 的第 6 阶段，所以技能不可能写出这个 actor 没有的 op。

**怎么加一个** —— 技能是一个目录，不需要注册。建 `skills/<名字>/SKILL.md`，头部写
`name` / `description`，正文先写方法（规则在前、支撑它的数字在后；机器特有的事实放附录，旁边写上
"怎么重新发现它"的命令），然后：

```powershell
.\tools\install-skills.ps1 -Check      # 镜像不同步时 exit 1
.\tools\install-skills.ps1             # 链接（或复制）进 $DSH_HOME\skills
.\tools\check-skill-ops.py -v          # 你写的每个 op 都必须在 actor/actor.py 里存在
```

除此之外不用手工接线：这个镜像**就是**安装，而 `tools/ci-static.ps1` 的第 6 阶段会在技能写出
actor 没有的 op 时直接判失败。要引用的 op 表在 [actor/README.md](actor/README.md)。

## 第一代：让 AI 看见屏幕（参考）

### 为什么需要它

DSH 的原生自看（self-look）插件是把页面 **DOM 序列化**成 PNG：它会剥掉 `<img> <svg> <canvas> <picture>`，也没有屏幕外的窗口、其它应用、系统托盘。结果就是——

- 页面里的图片、挂件、图表**拍不到**（实测：同一角落，DOM 截图标准差 0.004，几乎纯平色）；
- 截到的只是浏览器里的那一块，桌面别的东西一律不可见。

本仓库的插件改成走**原生截屏**（Win32 `CopyFromScreen` + 每显示器 DPI 感知），拍出来的就是屏幕真实像素；DOM 序列化作为兜底保留。

### 目录

```
actor/                        PC Actor —— 见 actor/README.md（现在的主路径）
skills/                       模型被告知的方法：<名字>/SKILL.md，镜像进 DSH_HOME
plugins/dsh-selflook-local/   DSH 插件：原生截屏 + shot RPC（上游 self-look 的加强分支）
                              ⚠ 默认没有安装；截图这条路已被 actor 的 shot op 取代
tools/
  template_match.py           FFT 归一化互相关模板匹配（零模型，像素级）
                              ⚠ 全屏场景已被 actor 的 find op 取代
  cv_ui_geometry.py           run-length 长直边检测 + 形态学实心块（零模型）
  ground_test.py              给 VLM 的框打分：6 种坐标解释全试一遍再取最优
  probe_and_ocr.py            单步对比度探针 + tesseract 裁剪 OCR
  ocr_boxes.py                tesseract TSV 包装：裁剪/放大/psm/反相，输出原图坐标
                              （actor 没有 OCR —— 这仍是唯一的读字路径）
  brightmap.py                区域亮度 ASCII 热力图，用来找面板与按钮边界
  reset-ollama.ps1            清掉占显存的孤儿 llama-server，重启唯一一个 serve
  dsh-look-native.ps1         通过插件 RPC 直接截一张图（需要插件在位）
  gui-steps.ps1               计划驱动的桌面 GUI 引擎 —— 「一个进程跑完一次交互」的最初实现（2026-10-01）
                              ⚠ 已被 actor run 取代，保留作参考实现
  contact-send.ps1            一条命令：搜索联系人 → 选中 → 模板确认真身 → 发送 → 像素复核
  pixel-verdict.py            无 OCR 的像素判据（输入框墨迹 / 气泡蓝）
docs/
  视觉能力实测报告.md          中文：结论表 + 复现命令 + 踩坑
  vision-capability-report.md  英文版
  界面自动化日志.md            中文：挂件表演 / QQ 代发 / 识别提速（含全部实测数字）
  windows-ollama-setup.md      Windows 上给 DSH 跑视觉模型的注意事项
examples/vision-router-tuned.yml   vision-router 配置块（含注释）
tests/score-pipeline.ps1      拿真值框给两条零模型管线打分
```

### 快速开始（第一代）

#### 1. 装插件（可选）

这个插件**默认没有安装**——只有你想要插件侧那条截图路径时才装。按上游 dsh-termux-kit 的约定，放进 profile 的 `local/`：

```powershell
$profile = "$env:USERPROFILE\.dsh\profiles\desktop"   # 按你的实际 profile 改
Copy-Item .\plugins\dsh-selflook-local "$profile\local\" -Recurse -Force
```

然后在 `$profile\package.json` 里加依赖与 bundle 条目：

```jsonc
{
  "dependencies": { "dsh-selflook-local": "file:./local/dsh-selflook-local" },
  "dsh": { "profile": { "bundles": [ /* … */ "dsh-selflook-local" ] } }
}
```

重启 DSH 生效（若 profile 的 `cordis.patch.yml` 里开着 `hmr.root: ["."]`，改动即时生效）。也可以用 DSH 自己的插件管理器安装同一个目录。

#### 2. 截一张图

```powershell
# 装了插件：
pwsh -File tools\dsh-look-native.ps1                     # 默认 http://127.0.0.1:19387

# 不装插件也行 —— actor 直接截：
.\actor\act.cmd '{"op":"shot","path":"shot.png"}'
```

插件也支持触发文件（`~/.dsh-look-request`，写 `dom` 则退回 DOM 序列化），并把最近一张图的路径写进 `~/.dsh-look-last.txt`。

#### 3. 用工具链

```powershell
$py = "python"   # 需要 numpy + Pillow
$shot = "shot.png"

# 模板匹配（有素材时最准）
& $py tools\template_match.py $shot .\assets\role.png --scales 0.34,0.3656,0.40 --expect 2337,1305,2561,1529

# 边框/布局（零素材）
& $py tools\cv_ui_geometry.py $shot --noise 3 --min-run 250 --annotate .\cv.png

# 给模型的框打分
& $py tools\ground_test.py --image $shot --model granite3.2-vision:2b --prompt "bounding box of the whale" --expect 2337,1305,2561,1529

# 一条命令跑两条零模型管线
pwsh -File tests\score-pipeline.ps1 -Image $shot -Expect 2337,1305,2561,1529
```

### 一代半：脚本驱动真实 GUI（2026-10-01，已被取代）

不止"看见"，还能**动手**。`tools/gui-steps.ps1` 用一份 JSON 计划驱动鼠标/键盘/剪贴板/截屏/模板匹配，整套交互跑在**一个 pwsh 进程**里——**它就是 PC Actor 的前身**；`actor run` 现在用常驻进程 + 结构查询 + 逐步计时做同一件事，新工作请用 actor。

- **代发消息实测**：一条命令完成"搜索联系人 → 选中 → 用会话头模板确认真身 → 发送 → 像素复核"，**1.89 s / 一次调用**；三条独立证据（气泡 OCR conf 94.9、新增时间分隔线、输入框清空且气泡蓝像素占该带 7.2%）。
- **两个真坑**：DSH 自己的窗口会不断抢回前台（必须 `SetWindowPos(HWND_TOPMOST)` 钉住目标窗口，收尾还原）；QQ 的发送键是 **Ctrl+Enter**，单回车只换行。
- **识别不再是瓶颈**：会话头识别从 tesseract 0.7 s/次 换成进程内 **ZNCC 模板匹配 ≈5 ms**（正样本 1.000，故意错位 400 px 只有 0.336，阈值 0.80），并且只抓 175×50 的会话头（≈5 ms）而不是整屏（≈0.25 s）。

完整过程、踩坑与复现命令见 [界面自动化日志](docs/界面自动化日志.md)。

## 实测结论（2026-10-01，2560×1600 原生截屏，真值 = DOM × dpr 1.5）

| 方法 | 目标 | 结果 | 能不能当尺子 |
|---|---|---|---|
| DOM `getBoundingClientRect` | 任意元素 | 挂件 root `2186,1154,375,375` 物理 | ✅ 精确（基准） |
| **模板匹配**（零模型） | 挂件角色图 | NCC 0.955，**IoU 0.991** | ✅ 像素级 |
| **长直边检测**（零模型） | 侧栏右边界 | `x=419`（真值 420，**差 1 px**，连续 1450 px） | ✅ 像素级 |
| tesseract `-l chi_sim+eng --psm 6` | 侧栏预算文字 | 免预处理读出「本月预算 ¥2.1346 / ¥100 余额 ¥107.17」 | ✅ 文字级 |
| 语义 blob / 色彩启发式 | 挂件框 | IoU 0.19–0.45 | ⚠️ 只能定性 |
| granite3.2-vision:2b | 挂件框 | `[0.81,0.78,0.9,0.82]`，IoU 0.000–0.004 | ❌ 不可用于测量 |
| qwen2.5vl:3b | 挂件框 | `[1059,582,1148,693]`，IoU 0.000 | ❌ 不可用于测量 |

**一句话**：几何量测靠像素算法（可到 1 px），文字靠 OCR，本地小 VLM 只配回答"大概在哪"。完整表、复现命令和踩坑见 [视觉能力实测报告](docs/视觉能力实测报告.md)。

## 环境要求

- Windows 10/11，PowerShell 5.1+。
- 用 actor：Python 3.9+ 与 numpy（`actor.ps1 -Setup` 会把 `comtypes` 装进 actor 的 HOME；
  客户端本身只用标准库）。Windows 的 UI Automation 由系统自带——不用 SDK、不用安装器、
  不用管理员权限。
- 用第一代的插件/工具：Win32 `SetThreadDpiAwarenessContext` + `CopyFromScreen`；装插件的
  话需要 DSH 桌面版（0.1.7 / 0.2.x）；Python 3 + numpy + Pillow；OCR 可选装
  [tesseract](https://github.com/UB-Mannheim/tesseract/wiki)（`chi_sim+eng`）。
- 本地视觉模型可选：Ollama + `qwen2.5vl:3b` / `granite3.2-vision:2b`，配置见 [windows-ollama-setup.md](docs/windows-ollama-setup.md)。

## 安全说明

- 插件在 DSH 的 web server 上注册 RPC 路由 `/__dsh__/selflook/rpc`。DSH 核心对插件路由不做鉴权，所以插件自己带**同源校验**（比对 `Sec-Fetch-Site` / `Origin` 与 `Host`，跨站直接 403），且输出目录由宿主固定，不接受调用方传入路径。截的是**整个屏幕**（含其它窗口），请自行确认使用场景。
- actor **只监听 `127.0.0.1:8731`**，且没有鉴权：任何能连上这个端口的东西都能移动鼠标、打字、
  读屏。它是本地自动化工具，不是对外服务。合成输入只会落到**有焦点**的窗口上，所以技能在动手
  前必须抢前台并钉住目标（`op window front|top`）。

## 致谢与许可

- 插件基于 [Maopk/dsh-termux-kit](https://github.com/Maopk/dsh-termux-kit) 的 `dsh-selflook-local` 分支改造；DPI 感知截屏脚本思路来自 `dsh-vision-router`。
- actor 走的是桌面自动化的标准配方（UIA 取结构、`SendInput` 发事件、像素兜底），形态上做成常驻引擎——与 RPA 代理、Chrome DevTools Protocol、Appium server 同一种架构。
- 仓库**不包含界面截图**（会暴露会话内容）；结论以数值 + 复现命令给出。
- MIT，见 [LICENSE](LICENSE)。
