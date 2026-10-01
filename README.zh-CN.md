# dsh-vision-kit

给 **DeepSeek Harness** 桌面版（Windows）用的工具包：让 AI 真的**看见屏幕，并且能动手**。仓库里同时住着两代东西：

| 代 | 是什么 | 状态 |
|---|---|---|
| **`actor/` —— PC Actor** | 常驻守护进程：**UIA 结构**取控件、`SendInput` 当手、像素方法兜底，**一次 `run` 调用跑完一整段技能**。每步 40–140 ms。 | **现在的主路径** |
| `plugins/`、`tools/`、`docs/`、`examples/`、`tests/` | 最初那套**零模型视觉工具链**——原生截屏插件、模板匹配、几何检测、OCR、VLM 打分——以及支撑所有数字的实测报告。 | 参考资料；**OCR / 几何 / VLM 打分仍是唯一路径** |

> 这份仓库里所有的能力声明都有真值打分，不是"感觉能用"。

- English: [README.md](README.md) · Actor 手册： [actor/README.md](actor/README.md) · 结论报告： [视觉能力实测报告](docs/视觉能力实测报告.md) · [English report](docs/vision-capability-report.md)

## 现在的主路径：PC Actor

`actor/` 是一个长驻进程，把屏幕、输入队列和自动化会话都握在手里，在 `127.0.0.1:8731` 上
一行 JSON 进、一行 JSON 出。它存在的理由：GUI 自动化里慢的从来不是识别，而是**那个循环**
——一次假设一次工具往返，而每次往返都要新起 shell 和冷启动 Python。

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
| 列出全部顶层窗口（名字/类/hwnd/矩形） | 2–4 s | **41 ms** |
| 按名字/自动化 ID 找控件 | 1–3 s | **59 ms**（一次调用拿 36 个按钮） |
| 点击（含人手缓动） | 1–2 s | **~120 ms** |
| 输入文本 / 回车 | 2–4 s / 1–2 s | **50 ms / 54 ms** |
| 全屏颜色连通域 | 1.1 s | **110 ms** |
| 全屏 60×60 模板匹配 | 573 ms | **91 ms** |
| 协议往返 / 客户端启动 | — | **12 ms / 107 ms** |
| 完整技能：启动计算器 → `7*8` → 读 56 → `12+30` → 点「等于」→ 读 42 | 几分钟、几十次调用 | **0.62 s**（热）/ **1.4 s**（含 UWP 冷启动），**读像素 0 次** |

细节、op 表、状态目录（`D:\DSH\dsh-actor`）与七条踩坑（`TreeScope_Descendants = 4`、
UIA 必须单 STA 线程独占 COM 且每次调用带超时、UWP 要用 `shell:appsFolder` 启动、
`SendInput` 只发给有焦点的窗口 → 先抢前台并钉住、等待要轮询结构通道而不是 sleep）：
[actor/README.md](actor/README.md)。

## 第一代：让 AI 看见屏幕

### 为什么需要它

DSH 的原生自看（self-look）插件是把页面 **DOM 序列化**成 PNG：它会剥掉 `<img> <svg> <canvas> <picture>`，也没有屏幕外的窗口、其它应用、系统托盘。结果就是——

- 页面里的图片、挂件、图表**拍不到**（实测：同一角落，DOM 截图标准差 0.004，几乎纯平色）；
- 截到的只是浏览器里的那一块，桌面别的东西一律不可见。

本仓库的插件改成走**原生截屏**（Win32 `CopyFromScreen` + 每显示器 DPI 感知），拍出来的就是屏幕真实像素；DOM 序列化作为兜底保留。

### 目录

```
actor/                        PC Actor —— 见 actor/README.md（现在的主路径）
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
