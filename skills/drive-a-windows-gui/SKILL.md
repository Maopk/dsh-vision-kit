---
name: drive-a-windows-gui
description: Use when a task needs a Windows GUI driven by mouse and keyboard (open an app, click through it, type into it, play something) — pick the cheapest channel first (config, database, CLI, deep link), then drive the screen with UIA-first targeting, wait_for instead of fixed sleeps, one verifying signal after every action, and loud failure with a screenshot.
---

# 驱动 Windows 图形界面（通用）

本机的手和眼睛是 `dsh-vision-kit` 的 actor：op 全表、每一项的坑和耗时基准写在 `actor/README.md`。
这份技能只写**跨任务成立的判断**，不写某个 app 的坐标。

## 0 先决定要不要碰像素

从上往下试，能停就停：

1. **结构通道** —— 配置文件、数据库、命令行参数、URL scheme、本地 API、插件或规则文件。
2. **日志与存档** —— 程序自己的 log、最近文件、历史/会话库；常常直接告诉你「上次做到哪」。
3. **深链** —— `HKCU:\Software\Classes\<scheme>`；没有就是没有（便携版和多数国产应用不注册）。
4. **像素** —— 鼠标键盘 + 截屏识别。

顺序做反是最大的浪费：一条深链 1 秒能做完的事，像素路线要几分钟。**先花 30 秒查第 1–3 层。**

## 1 感知：能用结构就别读图

- `state uia:true` 一次拿到前台窗口 + 顶层窗口 + 具名元素（名字 / 类型 / 矩形）。
- 元素有名字就用名字点（`target.uia` 或 `find`），没有才退到坐标。
- 截图只在两处用：验证「在不在动」（`probe`）、失败取证。
- Flutter 应用的 UIA 扫描偏慢（实测 1.2–1.7 s），但内容层通常在树里 —— 这个钱值得付；
  把一张 1920×1290 的 PNG 读进模型更贵。

## 2 等待：`wait_for` 不是 `sleep`

- 固定 `sleep` 是猜；`wait_for`（元素 / 颜色 / 模板出现，带超时）命中即走，`watch` 等画面变化。
- 实测：一次 11 步的 `run` 里 6 秒是我手写的 sleep；整条流程 280 秒里 93 秒是 sleep ——
  其中七八成本可省掉。

## 3 每个动作后立刻验一个信号

- 点击 → 页面或元素变了没；输入 → 文本框内容变了没；播放 → 画面在动没（`probe diff` 的百分比）。
- 结论要**机器可判**（百分比、退出码、元素存在），不要「看起来好了」。

## 4 一次 `run` 串完，别一步一停

- 一个 `run` = 一次往返：把「点 → 等 → 验」写进同一个步骤数组。
- 要读数据的步骤（`uia` / `state` / `probe` / `find`）在请求里加 **`"results": true`**：每步自己
  那份返回会跟着 trace 回来（长文本截断、长列表保留 6 项 + `…(N more items)`）。**不加就只有标量**，
  于是你被迫把那个 op 再发一次——这正是"一次假设一次往返"的老毛病（实测一次 280 秒的任务里，
  32 次工具调用有 10 次是「截图→读图」）。
- 从 PowerShell 直接把 JSON 递给客户端会被内层双引号吃掉（README 坑 10）→
  **把请求写进文件，再 `act.cmd run <文件>`**（`act.cmd <文件>` 也行）。
- 等待用 `wait_for`/`watch`，不要 `sleep` 猜时间（实测：手写的 sleep 占总时长 33%，真动作只占 7%）。

## 5 录制一次，之后回放

同一条流程做第二遍时，别再让模型现场走一遍：把跑成功的 `run` 存成宏，之后**一次调用**跑完。

```powershell
# 录制：请求里写 "record": "<名字>"，跑完且每步 ok 才存；重名要加 "overwrite": true
.\actor\act.cmd run D:\DSH\dsh-actor\tmp\calc.json
# 回放：一次调用 0.3 秒；"args" 覆盖宏里存的默认参数
.\actor\act.cmd '{"op":"macro","what":"run","name":"calc-eval","args":{"expr":"9*9"},"results":true}'
.\actor\act.cmd macro list      # 有哪些宏、多少步、回放过几次、上次多少毫秒、FAILED 过没
```

- 宏 = `$ACTOR_HOME\macros\<name>.json`：可读、可改、可进 git；字段 `steps / args / front / note / stats`。
- **参数化**：步骤里写 `{{expr}}`，默认值存在宏的 `args`，回放时按需覆盖；`as: "disp"` 把某一步的
  完整返回存进命名空间，后面用 `{{disp.hits.0.name}}` 取。整串就是一个占位符时**直接返回原值**，
  所以捕获到的 `rect` 能当坐标用。`"literal": true` 让某步完全不展开，`dry: true` 只展开不执行。
- **实测**（Calculator，7 步：提窗 → 点显示区 → Esc → 输入 → 回车 → 读回显 → 取消置顶）：
  录制 1.8 s；回放 **0.30 s**（服务端 0.20 + 客户端 0.10），三次回放得到 56 / 579 / 81。
  手工等价路线是 7 次往返，每次还要付模型延迟 + 1.2–2.6 s 的 UIA 扫描。
- 第一次回放会多花约 1 s：回复里的 `front_resolve_ms`（客户端表头 `front=`）是**第 0 步之前**解析
  前台窗口的耗时——守护进程或目标程序刚起来、窗口还没缓存时实测 **1361 ms**，缓存后 1–3 ms。
  它不在 `total_ms` 里，所以别把「第一次 1.3 s、后面 0.3 s」当成回放不稳。
- 快的原因是两条，也是写宏时该守的：窗口按标题**只解析一次**（1371 ms → 3 ms，窗口没了才重找）；
  UIA 选择器先在**当前窗口内**找（95 ms），找不到才退回全桌面（1500 ms）。
- 所以录制时优先用结构选择器（`aid` / `name`）：坐标和像素步骤录进去也会回放，但窗口一挪就错。
- 宏不自带启动：**目标窗口得先在**（要起 app 见 §0 第 3 层）。

## 6 失败要响

- 落截图 + 报路径 + 说清卡在哪一步，然后把控制权交回模型或用户。
- 已知必挂的路径先避开（实测：某个番剧源必然解析超时，白等 13 秒 ×2）。

## 7 升级线（什么时候别用这套）

- 登录、验证码、支付、不可逆操作 → 交给用户。
- 目标有原生 CLI 或 API → 回第 0 层。
- 同一条流程要重复做 → 别每次现场走：**录制一次，之后回放**（§5：命令 + 实测 0.30 s）。

## 附录 A：本机事实（以及怎么重新发现）

- actor 在 `D:\DSH\dsh-vision-kit\actor`（客户端 `act.cmd`，守护进程 `:8731`，首次调用自动拉起）；
  日志 `D:\DSH\dsh-actor\logs\actor.log`。
- 屏幕 2560×1600；窗口坐标 = 截图 `region` 原点 + 图内坐标。
- 已知：Kazumi 没有深链（注册表四处全空）；播放走 AGE 源可用、7sefun 必超时。
- 重新发现：`act.cmd ping` 看 geom/dpi；`act.cmd state uia:true` 看当前界面；`actor/README.md` 是权威。
