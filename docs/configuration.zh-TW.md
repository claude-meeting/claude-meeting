# 進階設定

[English](configuration.md) · [简体中文](configuration.zh-CN.md) · [回到說明](../README.zh-TW.md)

大部分人只需要 App 的設定視窗（選單列圖示 →「設定…」）。這一頁是其他所有設定。

## 設定檔

`~/.config/meeting-copilot/config.toml` 會覆蓋 [`app/config.example.toml`](../app/config.example.toml) 的預設值（每個設定都有說明）。常用的：

| 區段 | 內容 |
|---|---|
| `[user]` | 你的名字、別人怎麼稱呼你（用來判斷「問我的問題」）、語言：`zh-TW`（繁體中文）、`zh-CN`（簡體中文）或 `en`（筆記、回答、通知和語音辨識都跟著它；空白＝依 macOS 語言） |
| `[autopilot]` | 自動錄音開關、開始／停止的等待秒數、要忽略的 App、`manual_mode`：「開始錄音」用哪個模式（`auto`；`room` 實體會議與手機擴音；`call`） |
| `[copilot]`／`[explain]` | 模型、整理頻率、回答長度、網路查證 |
| `[llm]` | 誰來寫筆記與回答：你的 Claude 訂閱（預設），或任何 OpenAI 相容格式的服務 |
| `[notify]` | 要顯示哪些通知、音效、最短間隔 |
| `[audio]` | 固定麥克風，避免會議中藍牙耳機連上就被切走 |
| `[screen]` | 畫面擷取開關、所有螢幕／主螢幕／單一 App、是否把截圖給模型 |
| `[voice]` | 聲紋驗證與門檻 |
| `[retention]` | 錄音、截圖、筆記、逐字稿保留天數（0＝永久） |
| `[asr]` | 語音辨識模型（`auto` 依語言選）、麥克風音量門檻 |
| `[paths]` | 會議資料夾、Minutes、Claude Code 的位置 |
| `[privacy]` | 告知與會者的提醒、要不要保存其他人的聲紋 |

改完要重啟助手（設定視窗會自動做）。改了 `[user] language`、`[audio]`、`[privacy]` 或 `[paths]`，請再跑一次設定精靈（選單 →「設定精靈…」）：語言決定語音辨識模型，其餘會同步到 Minutes 的設定。

## 改用其他模型

筆記與回答預設由你的 Claude 訂閱產生。要改用別的服務，在「設定」→「筆記與回答用的模型」選「其他服務」，填網址、模型名稱和金鑰，按「測試連線」確認後儲存。也可以直接改設定檔：

```toml
[llm]
provider = "openai"                       # 任何支援 OpenAI 相容格式的服務
base_url = "https://api.deepseek.com/v1"  # 服務的網址，到 /v1 為止；本機模型例如 "http://localhost:11434/v1"
model = "deepseek-chat"                   # 那個服務上的模型名稱
fast_model = ""                           # 選填：較快、較便宜的模型，用來每幾秒檢查新句子有沒有問題
vision = false                            # 模型看得懂圖片時設 true，才會送出畫面截圖
```

金鑰放在 `~/.config/meeting-copilot/api_key`（只有一行；本機模型不需要），或設定環境變數 `MEETING_COPILOT_API_KEY`。改完重啟助手。

- 用哪個模型、效果如何，由你自己選擇與負責；提示詞是以 Claude 調校的。
- 改用其他服務後，會議的逐字稿與畫面文字會送到那個服務。
- 這種方式沒有「用網路搜尋查證回答」；`vision = false` 時也不會從畫面辨識發言者（仍會用聲音辨識）。

## 指令列

Mac App 的引擎在 `~/Library/Application Support/Meeting Copilot/engine`（用指令稿安裝的是 `~/meeting-copilot`），以下寫成 `$ENGINE`。

```bash
tail -F ~/meetings/live/current.md                                   # 看即時筆記
tail -f "$ENGINE/app/state/autopilot.log"                            # 看助手在做什麼
launchctl kickstart -k gui/$(id -u)/com.meeting-copilot.autopilot    # 重啟助手
"$ENGINE/app/bin/copilot-known" Kubernetes TypeScript                # 這些名詞不用再解釋
"$ENGINE/app/bin/copilot-name" B 毛毛蟲                                # 指定講者 B 是誰（下次會議自動認出）
```
