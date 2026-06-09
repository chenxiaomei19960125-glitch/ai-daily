# 📰 AI 早报 · 每日自动更新

每天下午 17:00 自动从公开 RSS 抓取 AI 圈资讯，部署到 GitHub Pages。

**线上地址**：https://chenxiaomei19960125-glitch.github.io/ai-daily/

## 数据原则
- 所有资讯条目均**保留原始来源链接**，可点击溯源
- 脚本**不调用 LLM 二次加工**，避免 AI 幻觉
- 「股市信号」「人才市场」「Takeaway」三个板块为人工每日补充

## 目录
```
ai-daily/
├── index.html              # 主页（右上角可切换日期）
├── data/
│   ├── index.json          # 日期索引
│   └── YYYY-MM-DD.json     # 每日数据
├── update_ai_daily.py      # 自动抓取脚本（17:00 由 launchd 触发）
└── com.chenxiaomei.ai-daily.plist  # launchd 配置
```

## 数据源（全部公开 RSS）
- 国内：量子位、机器之心、36氪 AI、InfoQ
- 海外官方：OpenAI、Anthropic、Google DeepMind
- 海外媒体：TechCrunch AI、The Verge AI

## 手动更新
```bash
cd /Users/chenxiaomei/CodeBuddy/ai-daily
python3 update_ai_daily.py
```

## 定时任务管理
```bash
# 加载
launchctl load ~/Library/LaunchAgents/com.chenxiaomei.ai-daily.plist
# 卸载
launchctl unload ~/Library/LaunchAgents/com.chenxiaomei.ai-daily.plist
# 立即触发一次
launchctl start com.chenxiaomei.ai-daily
```
