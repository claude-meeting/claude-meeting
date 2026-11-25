# 高级设置

[English](configuration.md) · [繁體中文](configuration.zh-TW.md) · [回到说明](../README.zh-CN.md)

大部分人只需要 App 的设置窗口（菜单栏图标 →“设置…”）。这一页是其他所有设置。

## 配置文件

`~/.config/meeting-copilot/config.toml` 会覆盖 [`app/config.example.toml`](../app/config.example.toml) 的默认值（每个设置都有说明）。常用的：

| 区段 | 内容 |
|---|---|
| `[user]` | 你的名字、别人怎么称呼你（用来判断“问我的问题”）、语言：`zh-TW`（繁体中文）、`zh-CN`（简体中文）或 `en`（笔记、回答、通知和语音识别都跟着它；空白＝跟随 macOS 语言） |
| `[autopilot]` | 自动录音开关、开始／停止的等待秒数、要忽略的 App、`manual_mode`：“开始录音”用哪个模式（`auto`；`room` 线下会议与手机免提；`call`）；录不到通话声音时：`call_audio_lost_secs`、`call_audio_restarts`（每场通话重新开始录音几次）、`fill_call_gaps`（会后把实时转写漏掉的通话内容补上） |
| `[copilot]`／`[explain]` | 模型、整理频率、回答长度、网络查证 |
| `[llm]` | 谁来写笔记与回答：你的 Claude 订阅（默认），或任何 OpenAI 兼容格式的服务 |
| `[notify]` | 要显示哪些通知、音效、最短间隔 |
| `[audio]` | 固定麦克风，避免会议中蓝牙耳机连上就被切走 |
| `[screen]` | 画面截取开关、所有屏幕／主屏幕／单一 App、是否把截图给模型 |
| `[voice]` | 声纹验证与阈值 |
| `[retention]` | 录音、截图、笔记、逐字稿保留天数（0＝永久） |
| `[asr]` | 语音识别模型（`auto` 按语言选）、麦克风音量阈值 |
| `[paths]` | 会议文件夹、Minutes、Claude Code 的位置 |
| `[privacy]` | 告知与会者的提醒、要不要保存其他人的声纹 |

改完要重启助手（设置窗口会自动做）。改了 `[user] language`、`[audio]`、`[privacy]` 或 `[paths]`，请再运行一次设置向导（菜单 →“设置向导…”）：语言决定语音识别模型，其余会同步到 Minutes 的设置。

## 改用其他模型

笔记与回答默认由你的 Claude 订阅产生。要改用别的服务，在“设置”→“笔记与回答用的模型”选“其他服务”，填地址、模型名称和密钥，按“测试连接”确认后保存。也可以直接改配置文件：

```toml
[llm]
provider = "openai"                       # 任何支持 OpenAI 兼容格式的服务
base_url = "https://api.deepseek.com/v1"  # 服务的地址，到 /v1 为止；本地模型例如 "http://localhost:11434/v1"
model = "deepseek-chat"                   # 那个服务上的模型名称
fast_model = ""                           # 可选：较快、较便宜的模型，用来每几秒检查新句子有没有问题
vision = false                            # 模型看得懂图片时设 true，才会送出画面截图
```

密钥放在 `~/.config/meeting-copilot/api_key`（只有一行；本地模型不需要），或设置环境变量 `MEETING_COPILOT_API_KEY`。改完重启助手。

- 用哪个模型、效果如何，由你自己选择与负责；提示词是针对 Claude 调优的。
- 改用其他服务后，会议的逐字稿与画面文本会送到那个服务。
- 这种方式没有“用网络搜索查证回答”；`vision = false` 时也不会从画面识别发言者（仍会用声音识别）。

## 命令行

Mac App 的引擎在 `~/Library/Application Support/Meeting Copilot/engine`（用脚本安装的是 `~/meeting-copilot`），以下写成 `$ENGINE`。

```bash
tail -F ~/meetings/live/current.md                                   # 看实时笔记
tail -f "$ENGINE/app/state/autopilot.log"                            # 看助手在做什么
launchctl kickstart -k gui/$(id -u)/com.meeting-copilot.autopilot    # 重启助手
"$ENGINE/app/bin/copilot-known" Kubernetes TypeScript                # 这些名词不用再解释
"$ENGINE/app/bin/copilot-name" B 毛毛虫                                # 指定说话人 B 是谁（下次会议自动认出）
```
