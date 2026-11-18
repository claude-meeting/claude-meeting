"""Language of everything the copilot shows or asks Claude: notifications, live notes, prompts.

[user] language in the config picks it ("zh-TW", "zh-CN", "en", ...); empty follows macOS. Three families
exist: "zh" (Traditional Chinese, Taiwan), "zh-hans" (Simplified Chinese, mainland usage) and "en". The Simplified
texts are made from the Traditional ones (to_hans below: OpenCC's Taiwan-to-mainland conversion, then HANS_FIXES),
so there is one text to keep up. Speaker tags in transcripts stay "我" / "其他人" internally (stored data); speaker()
turns them into the display language.
"""
import re
import subprocess

# Speaker tags as stored in transcripts (never shown as such: see Lang.speaker).
SELF, OTHERS = "我", "其他人"

DETAIL = {   # [explain] detail -> answer length in the prompt
    "zh": {"short": "一句話", "medium": "兩三句", "long": "詳細一點，五到八句"},
    "en": {"short": "one sentence", "medium": "two or three sentences", "long": "in detail, five to eight sentences"},
}

STRINGS = {
    "zh": {
        "user_fallback": "使用者",
        "me": "我", "others": "其他人",
        "meeting": "會議", "call": "通話",
        "call_title": "{app} 通話 {hhmm}",
        "none": "（無）",
        "screen_unchanged": "（畫面沒有變化）",
        "main_display": "主螢幕", "display_n": "螢幕 {n}",
        "screen_new_page": "【新畫面{where}】",
        "screen_update": "【畫面更新／捲動{where}，新出現的內容】",
        "image_note": "\n附上這段期間每一張有變化的畫面截圖（{n} 張，依時間順序）：分享的螢幕、"
                      "視訊鏡頭畫面、圖表、架構圖、程式碼或表格等 OCR 讀不完整的內容以圖片為準。\n",
        "notes_title": "# {title}（即時筆記）\n\n開始：{start}\n\n",
        "summary": "**摘要**：{text}",
        "item_mention": "🙋 點名", "item_my_todo": "✅ 我的待辦", "item_question_to_me": "❓ 問我",
        "item_decision": "📌 決策", "item_deadline": "⏰ 期限", "item_risk": "⚠️ 風險", "item_other_todo": "✅ 待辦",
        "answer_heading": "💡",
        "answer_to_me": "❓ 問我",
        "answer_generating": "回答產生中…",
        "video_link": "🎞️ 畫面回看：{path}",
        "speaker_named": "👤 {who} 是 {name}",
        "speaker_unnamed": "👤 {who} 不一定是 {name}（畫面上講話的不只一人），收回這個名字",
        "call_gap": "⚠️ {start}–{end} 通話聲音中斷，已重新錄音；這段沒有錄到",
        "record_gap": "（通話聲音中斷，約 {secs} 秒沒有錄到）",
        "speaker_moved": "👤 {time} 起那段的聲音和 {old} 不同，改標為 {new}",
        "speaker_line_unsure": "👤 {time} 那句畫面上是 {name} 在說，不標成 {old}",
        "list_sep": "、",
        "colon": "：",
        "title_sep": "｜",
        "n_claude_missing": "找不到 Claude Code（claude 指令）。請安裝 Claude Code，或在設定檔 [paths] claude_cli 指定路徑。",
        "n_claude_signed_out": "Claude Code 尚未登入。請在終端機執行 claude auth login，用你自己的 Claude 帳號（訂閱或 Console API）登入。",
        "n_llm_no_url": "還沒設定模型服務的網址。請在設定檔 [llm] 填 base_url，或把 provider 改回 \"claude\"。",
        "n_llm_no_model": "還沒設定要用的模型名稱。請在設定檔 [llm] 填 model。",
        "n_llm_rejected": "模型服務拒絕了金鑰。請到「設定」重新填金鑰。",
        "n_llm_failing": "模型服務連續出錯，請到「設定」按「測試連線」檢查網址和模型名稱。最後的錯誤：",
        "n_llm_unknown": "設定檔 [llm] 的 provider 只能是 \"claude\" 或 \"openai\"。在改好之前不會把內容送給任何模型。",
        "n_notes_paused": "⚠️ 即時筆記與說明暫停",
        "n_notes_paused_body": "{problem}逐字稿仍會照常記錄。",
        "n_minutes_unresponsive": "Minutes 沒有回應（錄音狀態未知）",
        "n_asr_missing": "目前語言的語音模型還沒安裝（{model}）：到「設定」精靈按「開始安裝」",
        "n_live_asr_stopped": "⚠️ 即時轉錄停止",
        "n_live_asr_stopped_body": "即時筆記暫停，會後的逐字稿不受影響；詳見 live_transcriber.log",
        "n_screen_denied": "⚠️ 畫面擷取沒有權限",
        "n_screen_denied_body": "請在「螢幕與系統錄音」打開 claude-meeting Screen（有些 Mac 列為 MeetingCopilotScreen）；暫時改用 Minutes 的截圖",
        "n_meeting_missing": "⚠️ 這場會議沒有會議紀錄",
        "n_meeting_missing_body": "「{title}」結束 {mins} 分鐘了，~/meetings 還沒有它的會議紀錄（即時筆記仍在）。請看 Minutes 的處理狀態：minutes jobs --all",
        "n_minutes_job_failed": "⚠️ 會後逐字稿沒有產生",
        "n_minutes_job_failed_body": "Minutes 處理「{title}」失敗，~/meetings 不會有這場會議的逐字稿與摘要（即時筆記仍在）。錯誤：{error}",
        "n_device_switched": "🎙️ 錄音裝置已切換",
        "n_call_audio_lost": "⚠️ 錄不到通話聲音",
        "n_call_audio_restarted_body": "通話聲音已有 {secs} 秒錄成靜音，已重新開始錄音（第 {n} 次）",
        "n_call_audio_lost_body": "通話聲音已有 {secs} 秒錄成靜音；請在選單按「停止錄音」再按「開始錄音」",
        "n_heard_name": "🙋 有人提到你",
        "n_recording_started": "🎙️ 開始自動錄音",
        "n_recording_started_body": "{title}：偵測到 {apps} 正在使用麥克風",
        "n_consent": "🔔 告知與會者",
        "n_consent_body": "這場會議正在錄音與轉錄，請告知與會者。",
        "n_recording_failed": "⚠️ 自動錄音失敗",
        "n_recording_failed_body": "{title}：{reason}（60 秒後重試）",
        "n_recording_stopped": "⏹️ 已停止錄音",
        "n_stop_failed": "停止錄音失敗，會再試一次",
        "n_recording_stopped_body": "{title}：通話結束，開始整理逐字稿與摘要",
        "n_minutes_done": "📝 會議紀錄完成",
    },
    "en": {
        "user_fallback": "the user",
        "me": "Me", "others": "Others",
        "meeting": "Meeting", "call": "call",
        "call_title": "{app} call {hhmm}",
        "none": "(none)",
        "screen_unchanged": "(screen unchanged)",
        "main_display": "main display", "display_n": "display {n}",
        "screen_new_page": "[New screen{where}]",
        "screen_update": "[Screen update / scroll{where}, newly visible]",
        "image_note": "\nAttached: every screen frame that changed in this period ({n}, oldest first): shared "
                      "screens, camera tiles, charts, diagrams, code or tables. Trust the images where OCR is incomplete.\n",
        "notes_title": "# {title} (live notes)\n\nStarted: {start}\n\n",
        "summary": "**Summary**: {text}",
        "item_mention": "🙋 Mentioned", "item_my_todo": "✅ My to-do", "item_question_to_me": "❓ Asked me",
        "item_decision": "📌 Decision", "item_deadline": "⏰ Deadline", "item_risk": "⚠️ Risk", "item_other_todo": "✅ To-do",
        "answer_heading": "💡",
        "answer_to_me": "❓ Asked me",
        "answer_generating": "Writing the answer…",
        "video_link": "🎞️ Screen recording: {path}",
        "speaker_named": "👤 {who} is {name}",
        "speaker_unnamed": "👤 {who} may not be {name} (the screen shows more than one person speaking): name withdrawn",
        "call_gap": "⚠️ {start}–{end} the call's audio was lost and the recording restarted: this stretch is missing",
        "record_gap": "(the call's audio was lost: about {secs} s not recorded)",
        "speaker_moved": "👤 The voice from {time} on is not {old}'s: now {new}",
        "speaker_line_unsure": "👤 At {time} the screen showed {name} talking: that line is not marked as {old}",
        "list_sep": ", ",
        "colon": ": ",
        "title_sep": " · ",
        "n_claude_missing": "Claude Code (the claude command) was not found. Install Claude Code, or set [paths] claude_cli in the config.",
        "n_claude_signed_out": "Claude Code is not signed in. Run claude auth login in Terminal with your own Claude account (subscription or Console API).",
        "n_llm_no_url": "The model service has no address yet. Set base_url under [llm] in the config, or set provider back to \"claude\".",
        "n_llm_no_model": "No model is named yet. Set model under [llm] in the config.",
        "n_llm_rejected": "The model service refused the key. Enter the key again in Settings.",
        "n_llm_failing": "The model service keeps failing: press Test in Settings to check the address and the model name. Last error:",
        "n_llm_unknown": "[llm] provider in the config must be \"claude\" or \"openai\". Until it is, nothing is sent to any model.",
        "n_notes_paused": "⚠️ Live notes and answers paused",
        "n_notes_paused_body": "{problem} The transcript is still being recorded.",
        "n_minutes_unresponsive": "Minutes is not responding (recording state unknown)",
        "n_asr_missing": "The speech model for this language is not installed ({model}): press Install in Setup",
        "n_live_asr_stopped": "⚠️ Live transcription stopped",
        "n_live_asr_stopped_body": "Live notes paused; the transcript after the meeting is not affected. See live_transcriber.log",
        "n_screen_denied": "⚠️ No permission to capture the screen",
        "n_screen_denied_body": "Turn on claude-meeting Screen (listed as MeetingCopilotScreen on some Macs) in Screen & System Audio Recording; using Minutes' screenshots for now",
        "n_meeting_missing": "⚠️ No meeting record for this meeting",
        "n_meeting_missing_body": "\"{title}\" ended {mins} minutes ago and ~/meetings still has no record of it (the live notes are kept). Check Minutes' jobs: minutes jobs --all",
        "n_minutes_job_failed": "⚠️ No transcript after the meeting",
        "n_minutes_job_failed_body": "Minutes could not process \"{title}\", so ~/meetings has no transcript or summary of it (the live notes are kept). Error: {error}",
        "n_device_switched": "🎙️ Recording device changed",
        "n_call_audio_lost": "⚠️ The call's audio is not being recorded",
        "n_call_audio_restarted_body": "The call's audio has been recorded as silence for {secs} s: the recording was restarted ({n})",
        "n_call_audio_lost_body": "The call's audio has been recorded as silence for {secs} s: choose Stop recording, then Start recording",
        "n_heard_name": "🙋 Someone mentioned you",
        "n_recording_started": "🎙️ Recording started",
        "n_recording_started_body": "{title}: {apps} is using the microphone",
        "n_consent": "🔔 Tell the participants",
        "n_consent_body": "This meeting is being recorded and transcribed; let the participants know.",
        "n_recording_failed": "⚠️ Recording failed to start",
        "n_recording_failed_body": "{title}: {reason} (retrying in 60 s)",
        "n_recording_stopped": "⏹️ Recording stopped",
        "n_stop_failed": "Could not stop the recording; trying again",
        "n_recording_stopped_body": "{title}: the call ended; writing the transcript and summary",
        "n_minutes_done": "📝 Meeting notes ready",
    },
}

# First line of an instant answer: which kind of line it was. Both families are accepted when parsing.
KINDS = {"zh": {"to_me": "問我", "question": "問題", "skip": "略過"},
         "en": {"to_me": "TO_ME", "question": "QUESTION", "skip": "SKIP"}}
# The line before the kind: whom the trigger line was said to, so the kind is chosen after saying it (with the
# kind first, the model could answer "問我" and only then write that the line asked nobody).
ADDRESSEE = ("對象", "ADDRESSEE")    # the Simplified word is added with the other Simplified texts below

ANALYSIS_PROMPT = {
    "zh": """你是 {name} 的即時會議助理，正在旁聽一場進行中的會議。
以下逐字稿來自本機語音辨識：可能是簡體字、有錯字或同音字（例如「權利」其實是「權力」、「期中」可能是「其中」）、沒有標點或講者，
也可能混入辨識幻覺（與上下文無關的句子，例如「祝你生日快樂」「謝謝觀看」「字幕製作」）或旁人不相干的談話。
請依語意理解；內容不合理、前後不連貫、或聽不出明確意思的片段不要當成事實，也不要據此產生提醒。

規則：
- 一律用台灣繁體中文回覆，專有名詞與英文術語保留原文。
- 只處理「新內容」區塊裡出現的新資訊；「先前內容」只用來理解脈絡。
- 不要重複「已經提醒過」清單裡的項目；意思相同、只是換句話說的也算重複。
- 歸屬要保守：只有逐字稿明確叫到 {name}（暱稱：{aliases}）、明顯是在對他說話，或該句標記為 [我] 時，才能把事情歸給 {name}。
  站會或輪流報告時，報告者預設不是 {name}，除非有人叫他的名字或該句標記為 [我]。不確定是誰時，owner 留空字串。
- 逐字稿若有 [我] / [其他人] 標記：[我] 是 {name} 本人說的話，[其他人] 是其他與會者；[其他人 A]、[其他人 B]
  是依聲音分出的不同與會者，已知名字時直接寫名字。
- items 的 type：
  - "mention"：有人點名 {name} 或要他做事、回應（說明對方要他做什麼）
  - "my_todo"：{name} 自己的待辦（他自己說要做、或明確交辦給他）
  - "question_to_me"：其他人明確在問 {name} 問題（叫了名字，或前後文清楚是對他說）；{name} 自己問的、問大家的不算
  - "decision"、"deadline"、"risk"：會議中的決策、期限、風險
  - "other_todo"：其他人的待辦（owner 寫是誰；不知道就空字串）
- answers（一般說明）：會議中提到可能不熟的名詞、縮寫、概念或技術、工具／產品／標準，或畫面上的圖表、數據、架構圖需要說明時，
  主動給出兩三句白話、可以直接在會議中說出口的說明（可用網路搜尋查證）；與任何程式碼 repo 無關。
  會議中明確的問句由另一個即時流程處理，這裡不用回答問句。每次最多 3 則，挑最可能需要的；
  「已經提醒過」裡 answered 的不要重複；不需要說明這些名詞：{known}；不確定時寧可不給。
- 畫面文字用來補充脈絡（例如簡報上的數字、期限、待辦、問題）；畫面上明確寫出的期限、決策或與 {name} 有關的項目也要列出。忽略會議軟體按鈕、選單等介面文字。
- 沒有值得列的就回空陣列，不要硬湊。

只輸出一個 JSON 物件，不要有其他文字或 code fence：
{{"summary": "新內容的一到兩句摘要（沒有新重點就空字串）",
 "items": [{{"type": "mention|my_todo|question_to_me|decision|deadline|risk|other_todo", "text": "一句話", "owner": "誰"}}],
 "answers": [{{"question": "問題或名詞", "answer": "兩三句解釋"}}]}}

已經提醒過：
{already}

先前內容：
{context}

新內容：
{new}

目前畫面上的文字（螢幕截圖經 OCR，可能是分享的簡報、文件或會議軟體介面；只在畫面有變化時提供）：
{screen}
{image_note}""",
    "en": """You are {name}'s live meeting assistant, listening to a meeting in progress.
The transcript below comes from on-device speech recognition: it may contain misheard words or homophones, lack
punctuation, include recognition hallucinations (sentences unrelated to the context, such as "Thanks for watching"
or "Please subscribe") or unrelated talk from people nearby.
Read for meaning; do not treat implausible, incoherent or unclear fragments as facts, and do not raise reminders from them.

Rules:
- Always reply in English; keep product names and technical terms as written.
- Only handle new information in the "New" section; "Earlier" is context only.
- Do not repeat anything in "Already reminded"; a rephrasing of the same point counts as a repeat.
- Be conservative about attribution: only attribute something to {name} when the transcript clearly names {name}
  (also called: {aliases}), is clearly addressed to them, or the line is tagged [Me].
  In stand-ups or round-robin updates the speaker is not {name} unless named or tagged [Me]. If unsure, leave owner empty.
- Tags: [Me] is {name}; [Others] is any other participant; [Others A], [Others B] are different participants
  told apart by voice, shown by name once known.
- Item types:
  - "mention": someone calls on {name} or asks them to do or answer something (say what is asked)
  - "my_todo": {name}'s own to-do (they said they will do it, or it was clearly assigned to them)
  - "question_to_me": someone else clearly asks {name} a question (named, or clearly said to them); not {name}'s own
    questions, nor questions to everyone
  - "decision", "deadline", "risk": decisions, deadlines and risks in the meeting
  - "other_todo": someone else's to-do (owner = who; empty if unknown)
- answers (general explanations): when the meeting mentions terms, acronyms, concepts, technologies, tools, products or
  standards that may be unfamiliar, or a chart, figure or diagram on screen needs explaining, give two or three plain
  sentences {name} could say out loud (you may check with web search); this is unrelated to any code repository.
  Explicit questions are handled by a separate instant flow; do not answer them here. At most 3 per call, the most
  useful ones; do not repeat answered items in "Already reminded"; never explain these terms: {known}; when unsure, give none.
- Screen text adds context (numbers, deadlines, to-dos, questions on slides); deadlines, decisions or items concerning
  {name} that are written on screen are listed too. Ignore meeting-app buttons and menus.
- If nothing is worth listing, return empty arrays.

Output exactly one JSON object, nothing else, no code fence:
{{"summary": "one or two sentences on the new content (empty string if nothing new)",
 "items": [{{"type": "mention|my_todo|question_to_me|decision|deadline|risk|other_todo", "text": "one sentence", "owner": "who"}}],
 "answers": [{{"question": "question or term", "answer": "two or three sentences"}}]}}

Already reminded:
{already}

Earlier:
{context}

New:
{new}

Text currently on screen (OCR of screenshots: shared slides, documents or the meeting app; only when the screen changed):
{screen}
{image_note}""",
}

URGENT_PROMPT = {
    "zh": """你是 {name} 的即時會議助理。會議進行中，下面「觸發句」剛被說出來。
逐字稿來自語音辨識，可能有錯字、同音字或簡體字；[我] 是 {name} 本人說的，[其他人] 是其他與會者。

請判斷觸發句屬於哪一種，並立刻回答：
- 問我：其他人明確在問 {name} 問題或要他回應（叫了 {name} 的名字，或前後文清楚顯示是在對 {name} 說；
  只有「你」但看不出是對誰說、問「你們」或大家、或明顯是在問其他與會者時，不算問我，有技術內容就選問題）
  → 依會議前後文與畫面內容，給 {name} 一個可以直接說出口的回答方向（2–3 句）。
- 問題：觸發句本身明確提出技術或知識性問題 → 給可以直接說出口的答案（{detail}）。
- 略過：其他所有情況。會議裡大多數句子都是略過：陳述、報告、指示、附和、語氣詞、沒說完的半句、聽不出意思的句子，
  已經有人回答的問題，以及和下面「剛回答過」問的是同一件事（換了說法也算）。只有觸發句本身清楚在問，才選「問我」或「問題」；
  拿不準就選略過。
不需要解釋這些名詞：{known}
剛回答過（最近幾分鐘）：{answered}

輸出格式（純文字，不要 markdown 標題或 code fence）：
第一行寫「對象：」，接著寫觸發句是對誰說的（{name}、大家、某位與會者，或看不出來）、在做什麼（提問、陳述、說明、指示），
例如「對象：大家，在說明進度」。
第二行依第一行只寫「問我」、「問題」或「略過」：不是對 {name} 說的就不是問我，不是在提問就是略過。
第三行寫一句話重述對方問了什麼。
第四行起寫回答。用台灣繁體中文，專有名詞與英文術語保留原文。

會議前後文：
{context}

畫面上的文字（OCR，可能沒有）：
{screen}

觸發句：
{line}
{hint}""",
    "en": """You are {name}'s live meeting assistant. The meeting is in progress and the "Trigger" line below was just said.
The transcript comes from speech recognition and may contain misheard words; [Me] is {name}, [Others] is any other participant.

Decide which kind the trigger line is and answer right away:
- TO_ME: someone clearly asks {name} a question or wants a response ({name} is named, or the context clearly shows
  it is said to {name}; a bare "you" with no clear addressee, a question to everyone, or a question to another
  participant is not TO_ME: choose QUESTION if it is technical) →
  from the meeting context and the screen, give {name} a reply direction they can say out loud (2–3 sentences).
- QUESTION: the trigger line itself clearly asks a technical or factual question → give an answer that can be said out
  loud ({detail}).
- SKIP: everything else. Most lines in a meeting are SKIP: statements, updates, instructions, agreement, filler words,
  unfinished sentences, unintelligible lines, questions already answered, and the same thing as one under "Just
  answered" (in other words too). Only choose TO_ME or QUESTION when the trigger line itself clearly asks; when unsure,
  choose SKIP.
Never explain these terms: {known}
Just answered (last few minutes): {answered}

Output format (plain text, no markdown headings or code fence):
First line: "Addressee:", then whom the trigger line is said to ({name}, everyone, another participant, or unclear) and
what it does (asks, states, explains, instructs), e.g. "Addressee: everyone, giving an update".
Second line, following the first: only TO_ME, QUESTION or SKIP. Not said to {name} is not TO_ME; not asking is SKIP.
Third line: one sentence restating what was asked.
From the fourth line: the answer, in English; keep product names and technical terms as written.

Meeting context:
{context}

Text on screen (OCR, may be empty):
{screen}

Trigger:
{line}
{hint}""",
}

# Said after the trigger line when the batch check already judged it a question: answer it.
JUDGED_HINT = {
    "zh": "（前一步已經判斷這一句是需要回答的問題：除非已經有人回答、完全聽不出意思，或它其實是陳述、說明而不是提問，"
          "不要選略過。）\n",
    "en": "(A first pass already judged this line a question that needs an answer: do not choose SKIP unless it was "
          "already answered, is unintelligible, or is in fact a statement rather than a question.)\n",
}

# Said after the trigger line: who spoke just before it. A bare "you" is mostly said to them.
PREVIOUS_HINT = {
    "zh": {"previous_me": "（觸發句之前最後一位發言的是 [{prev}]，也就是 {name} 本人：沒叫名字的「你」可能是在問 {name}。）\n",
           "previous_other": "（觸發句之前最後一位發言的是 [{prev}]，不是 {name}：沒叫名字的「你」通常是指這位剛發言、"
                             "剛報告或正在分享的人，不算問我；除非前後文清楚顯示是在對 {name} 說。）\n"},
    "en": {"previous_me": "(The last one to speak before the trigger line was [{prev}], that is {name}: a bare \"you\" may "
                          "be asking {name}.)\n",
           "previous_other": "(The last one to speak before the trigger line was [{prev}], not {name}: a bare \"you\" is "
                             "mostly said to that person, who just spoke, gave an update or is sharing, and is not TO_ME "
                             "unless the context clearly shows it is said to {name}.)\n"},
}

# Judge the lines of the last few seconds together, with context: which need an instant answer.
WINDOW_PROMPT = {
    "zh": """你在即時會議中幫 {name}（也被叫做：{aliases}）找出需要馬上協助的句子。
逐字稿來自語音辨識，常被切成半句、有錯字；[我] 是 {name}，[其他人] 是其他與會者。一個問題可能被切成好幾行，
請連起來讀，標在問題結束的那一行。

M＝有人明確在問 {name} 或要 {name} 回應（叫了名字，或前後文清楚是對 {name} 說；問「你們」或大家的不算）。
Q＝有人提出意思清楚的問題（技術、知識、數字、做法、選哪個），{name} 會想馬上知道答案。
其他都不標：陳述、報告、指示、附和、沒說完又沒接下去的半句、聽不懂的句子、問其他人自己的安排、已經有人回答的問題。

只標「新內容」裡的行。每個要標的行輸出一行「行號 代號」，例如「12 Q」；都不用標就輸出「無」。不要其他文字。

先前內容：
{context}

新內容：
{new}
""",
    "en": """You help {name} (also called: {aliases}) in a live meeting by finding lines that need help right now.
The transcript comes from speech recognition and is often cut into half sentences with misheard words; [Me] is {name},
[Others] is any other participant. One question may be split over several lines: read them together and mark the line
where the question ends.

M = someone clearly asks {name} something or wants {name} to respond (named, or clearly said to {name}; not a question
    to everyone).
Q = someone asks a clear question (technical, factual, numbers, how to, which one) that {name} would want answered now.
Mark nothing else: statements, updates, instructions, agreement, half sentences that go nowhere, unintelligible lines,
questions about another participant's own plans, questions already answered.

Only mark lines in "New". For each line to mark, output one line "number code", e.g. "12 Q"; if none, output "none".
Nothing else.

Earlier:
{context}

New:
{new}
""",
}

VERIFY_PROMPT = {
    "zh": """以下是會議中即時給出的回答。請用網路搜尋查證。
如果回答正確且完整，只輸出「OK」。
如果有錯誤或重要遺漏，輸出一到兩句更正或補充（台灣繁體中文，不要重複原本正確的部分）。

問題：{question}
回答：{answer}
""",
    "en": """Below is an answer given live in a meeting. Check it with web search.
If it is correct and complete, output only "OK".
If it has an error or an important omission, output one or two sentences of correction or addition (in English; do not
repeat what was already correct).

Question: {question}
Answer: {answer}
""",
}

VERIFY_LABEL = {"zh": "🔎 查證補充", "en": "🔎 Checked"}

HANS = "zh-hans"
# After OpenCC's Taiwan-to-mainland conversion (tw2sp): what it gets wrong for these texts, and what only made
# sense in Traditional Chinese. Checked by hand on every text; test_i18n keeps the result clean.
HANS_FIXES = (
    ("台湾繁体中文", "简体中文"),                          # "answer in Taiwan's Traditional Chinese"
    ("可能是简体字、有错字或同音字", "可能有错字或同音字"),  # Simplified output is expected here, not a mistake
    ("可能有错字、同音字或简体字", "可能有错字或同音字"),
    ("接口", "界面"),                                     # 介面 is a screen's interface (界面), not an API (接口)
    ("缺省", "默认"),
    ("辨识", "识别"), ("帐号", "账号"), ("终端机", "终端"),  # Taiwan usage tw2sp leaves as it is
    # Read through every text: Taiwan or dated usage, and macOS's own Simplified names.
    ("屏幕与系统录音", "录屏与系统录音"), ("登录项目", "登录项"), ("垃圾桶", "废纸篓"),   # as macOS names them
    ("结束 claude-meeting", "退出 claude-meeting"),          # the menu item that quits the app
    ("设置档", "配置文件"), ("背景程序", "后台程序"), ("没有回应", "没有响应"), ("精灵", "向导"),
    ("截屏", "截图"), ("视频镜头", "摄像头"), ("卷动", "滚动"), ("暱称", "昵称"), ("侦测", "检测"),
    ("产生中", "生成中"), ("会议纪录", "会议记录"), ("讲者", "说话人"), ("前后文", "上下文"),
    ("脉络", "背景"), ("语意", "语义"), ("依时间顺序", "按时间顺序"),
    # Whole phrases, never a bare 即时 or 依 (立即时间, 依然): tests/mainland_usage.py makes a new 即时 or 套用 fail.
    ("即时笔记", "实时笔记"), ("即时回答", "实时回答"), ("即时会议", "实时会议"), ("即时转录", "实时转录"),
    ("即时流程", "实时流程"), ("即时给出", "实时给出"), ("即时整理", "实时整理"),
    ("会套用到", "会应用到"), ("延伸功能", "扩展"),
    ("是依声音分出", "是按声音分出"), ("第二行依第一行", "第二行按第一行"), ("依会议上下文", "根据会议上下文"),
    ("请依语义理解", "请按语义理解"), ("拷贝", "复制"), ("安装档", "安装包"),
    ("本机", "本地"), ("网址", "地址"), ("选填", "可选"),
    ("「设置」向导", "「设置向导」"),                          # 「設定」精靈 is one name: the setup guide
    ("「", "“"), ("」", "”"), ("『", "‘"), ("』", "’"),                   # last: the fixes above match 「」
)
# The tables that have a Simplified version, written from their Traditional one ("zh") when this module loads.
HANS_TABLES = ("STRINGS", "DETAIL", "KINDS", "ANALYSIS_PROMPT", "URGENT_PROMPT", "JUDGED_HINT", "PREVIOUS_HINT",
               "WINDOW_PROMPT", "VERIFY_PROMPT", "VERIFY_LABEL")
_TO_HANS = _FOLD = None


def to_hans(text):
    """Traditional Chinese (Taiwan) to Simplified Chinese with mainland usage (软件, 屏幕, 设置...)."""
    global _TO_HANS
    if _TO_HANS is None:
        import opencc
        _TO_HANS = opencc.OpenCC("tw2sp")
    out = _TO_HANS.convert(text)
    for wrong, right in HANS_FIXES:
        out = out.replace(wrong, right)
    return out


def _hans(value):
    if isinstance(value, str):
        return to_hans(value)
    if isinstance(value, dict):
        return {k: _hans(v) for k, v in value.items()}
    return value


def fold_script(text):
    """The text in Simplified script, word for word (OpenCC t2s): to compare a name typed in one script with a
    transcript or a reply in the other (陳偉 / 陈伟)."""
    global _FOLD
    if _FOLD is None:
        import opencc
        _FOLD = opencc.OpenCC("t2s")
    return _FOLD.convert(text)


for _name in HANS_TABLES:
    globals()[_name][HANS] = _hans(globals()[_name]["zh"])
ADDRESSEE = (ADDRESSEE[0], to_hans(ADDRESSEE[0]), *ADDRESSEE[1:])


def macos_language():
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    first = next((l.strip().strip('",') for l in out.splitlines() if l.strip() not in ("(", ")")), "")
    return first


def family(language):
    """"zh-hans" for Simplified Chinese (zh-CN, zh-SG, zh-MY, zh-Hans...), "zh" for any other Chinese setting
    (zh-TW, zh-HK, zh-Hant, plain zh), else "en" (the fallback for languages without a translation yet)."""
    parts = (language or "").lower().replace("_", "-").split("-")
    if parts[0] != "zh":
        return "en"
    if "hans" in parts or ("hant" not in parts and any(p in ("cn", "sg", "my") for p in parts[1:])):
        return HANS
    return "zh"


def chinese(language_family):
    """Either Chinese family: full-width punctuation, Chinese speech recognition."""
    return language_family.startswith("zh")


class Lang:
    def __init__(self, cfg):
        self.code = cfg.get("user", {}).get("language") or macos_language() or "en"
        self.family = family(self.code)
        self.s = STRINGS[self.family]

    def t(self, key, **kw):
        return self.s[key].format(**kw) if kw else self.s[key]

    def speaker(self, tag, who=None, names=None):
        """Display tag: 我 / 其他人, or for another participant their name, else 其他人 A."""
        if tag == OTHERS and who:
            return (names or {}).get(who) or f"{self.s['others']} {who}"
        return {SELF: self.s["me"], OTHERS: self.s["others"]}.get(tag, tag)

    def detail(self, value):
        """[explain] detail: short / medium / long, or the user's own wording."""
        return DETAIL[self.family].get(value, value or DETAIL[self.family]["medium"])

    @staticmethod
    def header(lines):
        """(kind, restated question, lines used, addressee line) from an instant answer's first non-empty lines, or
        None while more are needed. The line naming whom the trigger line was said to may come first ("" without it);
        a skip needs no question. A reply without a kind is a skip: a reply that reasons first instead of naming its
        kind almost always needed no answer, so it is not shown as a question."""
        i = 1 if lines and lines[0].upper().startswith(ADDRESSEE) else 0
        if len(lines) <= i:
            return None
        addressee = lines[0] if i else ""
        kind = Lang.kind_of(lines[i])
        if kind in ("skip", None):
            return "skip", "", i + 1, addressee
        if len(lines) < i + 2:
            return None
        return kind, lines[i + 1], i + 2, addressee

    @staticmethod
    def kind_of(first_line):
        """'to_me' / 'question' / 'skip' / None from an instant answer's first line, in either language
        ("1. 略過" and "第二行：問題" read too)."""
        line = re.sub(r"^(?:\d+[.)、]|第.行[:：]|line \d+:)\s*", "", first_line.strip(), flags=re.I).upper()
        for words in KINDS.values():
            for kind, word in words.items():
                if line.startswith(word.upper()):
                    return kind
        return None
