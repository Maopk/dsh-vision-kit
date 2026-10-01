# dsh-vision-kit

给 **DeepSeek Harness** 桌面版（Windows）用的「让 AI 真的看见屏幕」工具包：一个带**原生截屏**的自看插件，加一套**不依赖模型**的几何/OCR 工具链，以及一份**实测量化**的能力报告。

> 这份仓库里所有的能力声明都有真值打分，不是"感觉能用"。

- English: [README.md](README.md) · 结论报告： [视觉能力实测报告](docs/视觉能力实测报告.md) · [English report](docs/vision-capability-report.md)

## 为什么需要它

DSH 的原生自看（self-look）插件是把页面 **DOM 序列化**成 PNG：它会剥掉 `<img> <svg> <canvas> <picture>`，也没有屏幕外的窗口、其它应用、系统托盘。结果就是——

- 页面里的图片、挂件、图表**拍不到**（实测：同一角落，DOM 截图标准差 0.004，几乎纯平色）；
- 截到的只是浏览器里的那一块，桌面别的东西一律不可见。

本仓库的插件改成走**原生截屏**（Win32 `CopyFromScreen` + 每显示器 DPI 感知），拍出来的就是屏幕真实像素；DOM 序列化作为兜底保留。

## 目录

```
plugins/dsh-selflook-local/   DSH 插件：原生截屏 + shot RPC（上游 self-look 的加强分支）
tools/
  template_match.py           FFT 归一化互相关模板匹配（零模型，像素级）
  cv_ui_geometry.py           run-length 长直边检测 + 形态学实心块（零模型）
  ground_test.py              给 VLM 的框打分：6 种坐标解释全试一遍再取最优
  probe_and_ocr.py            单步对比度探针 + tesseract 裁剪 OCR
  dsh-look-native.ps1         通过插件 RPC 直接截一张图，打印路径
  reset-ollama.ps1            清掉占显存的孤儿 llama-server，重启唯一一个 serve
  gui-steps.ps1               计划驱动的桌面 GUI 引擎（鼠标/键盘/剪贴板/截屏/ZNCC 模板匹配）
  contact-send.ps1            一条命令：搜索联系人 → 选中 → 模板确认真身 → 发送 → 像素复核
  pixel-verdict.py            无 OCR 的像素判据（输入框墨迹 / 气泡蓝）
  ocr_boxes.py                tesseract TSV 包装：裁剪/放大/psm/反相，输出原图坐标
  brightmap.py                区域亮度 ASCII 热力图，用来找面板与按钮边界
docs/
  视觉能力实测报告.md          中文：结论表 + 复现命令 + 踩坑
  vision-capability-report.md  英文版
  界面自动化日志.md            中文：挂件表演 / QQ 代发 / 识别提速（含全部实测数字）
  windows-ollama-setup.md      Windows 上给 DSH 跑视觉模型的注意事项
examples/vision-router-tuned.yml   vision-router 配置块（含注释）
tests/score-pipeline.ps1      拿真值框给两条零模型管线打分
```

## 快速开始

### 1. 装插件

按上游 dsh-termux-kit 的约定，放进 profile 的 `local/`：

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

### 2. 截一张图

```powershell
# 方式 A：一次 HTTP，立即出图（推荐）
pwsh -File tools\dsh-look-native.ps1                     # 默认 http://127.0.0.1:19387
pwsh -File tools\dsh-look-native.ps1 -Port 3080

# 方式 B：写触发文件，插件轮询到就截（兼容老流程）
Set-Content "$env:USERPROFILE\.dsh-look-request" ""      # 原生截屏
Set-Content "$env:USERPROFILE\.dsh-look-request" "dom"   # 退回 DOM 序列化
```

两种方式都会把最近一张图的路径写进 `~/.dsh-look-last.txt`，PNG 落在 `~/Downloads/dsh/图片/`。

### 3. 用工具链

```powershell
$py = "python"   # 需要 numpy + Pillow
$shot = Get-Content "$env:USERPROFILE\.dsh-look-last.txt"

# 模板匹配（有素材时最准）
& $py tools\template_match.py $shot .\assets\role.png --scales 0.34,0.3656,0.40 --expect 2337,1305,2561,1529

# 边框/布局（零素材）
& $py tools\cv_ui_geometry.py $shot --noise 3 --min-run 250 --annotate .\cv.png

# 给模型的框打分
& $py tools\ground_test.py --image $shot --model granite3.2-vision:2b --prompt "bounding box of the whale" --expect 2337,1305,2561,1529

# 一条命令跑两条零模型管线
pwsh -File tests\score-pipeline.ps1 -Image $shot -Expect 2337,1305,2561,1529
```

## 界面自动化：脚本驱动真实 GUI（2026-10-01）

不止"看见"，还能**动手**。`tools/gui-steps.ps1` 用一份 JSON 计划驱动鼠标/键盘/剪贴板/截屏/模板匹配，整套交互跑在**一个 pwsh 进程**里：

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

- Windows 10/11（原生截屏走 Win32 `SetThreadDpiAwarenessContext` + `CopyFromScreen`），PowerShell 5.1+。
- DSH 桌面版（0.1.7 / 0.2.x 均可），插件走 bundle 机制。
- 工具链：Python 3 + numpy + Pillow；OCR 可选装 [tesseract](https://github.com/UB-Mannheim/tesseract/wiki)（`chi_sim+eng`）。
- 本地视觉模型可选：Ollama + `qwen2.5vl:3b` / `granite3.2-vision:2b`，配置见 [windows-ollama-setup.md](docs/windows-ollama-setup.md)。

## 安全说明

插件在 DSH 的 web server 上注册 RPC 路由 `/__dsh__/selflook/rpc`。DSH 核心对插件路由不做鉴权，所以插件自己带**同源校验**（比对 `Sec-Fetch-Site` / `Origin` 与 `Host`，跨站直接 403），且输出目录由宿主固定，不接受调用方传入路径。截的是**整个屏幕**（含其它窗口），请自行确认使用场景。

## 致谢与许可

- 插件基于 [Maopk/dsh-termux-kit](https://github.com/Maopk/dsh-termux-kit) 的 `dsh-selflook-local` 分支改造；DPI 感知截屏脚本思路来自 `dsh-vision-router`。
- 仓库**不包含界面截图**（会暴露会话内容）；结论以数值 + 复现命令给出。
- MIT，见 [LICENSE](LICENSE)。
