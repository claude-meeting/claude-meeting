"""Taiwan usage that must not be left in Simplified Chinese texts, written in Simplified script (tw2sp converts the
characters but keeps the Taiwan word: 软体 for 软件, 即时 for 实时...). Shared by test_logic (the app's and the engine's
texts) and test_demo_meeting (the Simplified demo meeting); imports nothing."""

TW_USAGE = ("軟體", "软体", "螢幕", "萤幕", "設定", "设定", "網路", "网路", "程式", "资料夹", "视窗", "登入",
            "帐号", "品质", "支援", "回馈", "预设", "滑鼠", "列印", "搜寻", "伺服器", "影片", "专案", "透过",
            "使用者", "介面", "储存", "讯息", "音讯", "视讯", "资讯", "撷取", "金钥", "选单", "辨识", "终端机",
            "物件", "字串", "阵列", "图示", "台湾", "繁体", "简体字", "「", "」", "接口", "缺省",
            "设置档", "背景程序", "截屏", "讲者", "垃圾桶", "登录项目", "精灵", "前后文", "脉络", "卷动", "侦测",
            "本机", "暱", "拷贝", "安装档", "会议纪录", "视频镜头", "屏幕与系统录音", "即时", "套用", "延伸功能",
            "依声音", "依语义", "依会议", "依第一行")
