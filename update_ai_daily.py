#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI 早报每日自动更新脚本
每天下午 17:00 由 launchd 触发：
  1. 从多个真实 RSS / 公开源抓取当日 AI 资讯
  2. 按板块分类、去重
  3. 生成 data/YYYY-MM-DD.json（每条带原始链接）
  4. 更新 data/index.json
  5. git add / commit / push 到 GitHub Pages

设计原则：
- 不调 LLM、不二次加工内容 → 杜绝 AI 幻觉
- 每条都保留原标题 + 真实链接 + 来源媒体
- 失败时退化保留昨日数据，不破坏页面
"""
import os, sys, json, re, time, hashlib, traceback
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen, Request
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, 'data')
os.makedirs(DATA_DIR, exist_ok=True)
LOG = os.path.join(ROOT, 'update.log')

CN_TZ = timezone(timedelta(hours=8))

def log(msg):
    line = f"[{datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

# ============== 真实 RSS / 公开源（无需 API key） ==============
SOURCES = [
    # 国内媒体
    {"name": "量子位", "url": "https://www.qbitai.com/feed", "section": "media"},
    {"name": "机器之心", "url": "https://www.jiqizhixin.com/rss", "section": "media"},
    {"name": "36氪 · AI",  "url": "https://36kr.com/feed-newsflash", "section": "media"},
    {"name": "InfoQ 中国", "url": "https://www.infoq.cn/feed.xml", "section": "media"},
    # 海外官方
    {"name": "OpenAI 官方博客", "url": "https://openai.com/blog/rss.xml", "section": "global"},
    {"name": "Anthropic News",  "url": "https://www.anthropic.com/rss.xml", "section": "global"},
    {"name": "Google DeepMind",  "url": "https://deepmind.google/blog/rss.xml", "section": "global"},
    # 海外媒体
    {"name": "TechCrunch · AI",  "url": "https://techcrunch.com/category/artificial-intelligence/feed/", "section": "global"},
    {"name": "The Verge · AI",   "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "section": "global"},
]

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

def fetch(url, timeout=15):
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urlopen(req, timeout=timeout) as r:
        return r.read()

def strip_html(s):
    s = re.sub(r'<[^>]+>', '', s or '')
    s = re.sub(r'\s+', ' ', s)
    return s.strip()

def parse_rss(xml_bytes):
    """简易 RSS / Atom 解析。返回 [{title, link, summary, pub_dt}]"""
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return items
    # 移除 namespace
    for el in root.iter():
        if '}' in el.tag:
            el.tag = el.tag.split('}', 1)[1]

    # RSS 2.0
    for it in root.iter('item'):
        title = (it.findtext('title') or '').strip()
        link  = (it.findtext('link') or '').strip()
        desc  = strip_html(it.findtext('description') or '')
        pub   = (it.findtext('pubDate') or '').strip()
        items.append({"title": title, "link": link, "summary": desc, "pub": pub})
    # Atom
    for it in root.iter('entry'):
        title = (it.findtext('title') or '').strip()
        link_el = it.find('link')
        link = link_el.get('href') if link_el is not None and link_el.get('href') else (it.findtext('link') or '').strip()
        desc = strip_html(it.findtext('summary') or it.findtext('content') or '')
        pub  = (it.findtext('updated') or it.findtext('published') or '').strip()
        items.append({"title": title, "link": link, "summary": desc, "pub": pub})
    return items

def is_recent(pub_str, days=2):
    if not pub_str: return True
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(pub_str, fmt)
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt) <= timedelta(days=days)
        except: pass
    return True

# ============== 抓取主流程 ==============
def collect():
    today = datetime.now(CN_TZ).strftime('%Y-%m-%d')
    pools = {"global": [], "media": []}
    seen_titles = set()

    for src in SOURCES:
        try:
            log(f"抓取 {src['name']} …")
            xml = fetch(src['url'])
            items = parse_rss(xml)
            cnt = 0
            for it in items:
                if not it.get('title') or not it.get('link'): continue
                if not is_recent(it.get('pub'), days=2): continue
                key = re.sub(r'\s+', '', it['title'])[:30]
                if key in seen_titles: continue
                seen_titles.add(key)
                desc = (it.get('summary') or '')[:160]
                pools[src['section']].append({
                    "title": it['title'],
                    "desc": desc if desc else "（点击「来源」查看原文）",
                    "takeaway": "",
                    "sources": [{"name": src['name'], "url": it['link']}]
                })
                cnt += 1
                if cnt >= 6: break
        except Exception as e:
            log(f"  失败：{e}")

    # 每个板块最多保留 8 条
    return {
        "global": pools["global"][:8],
        "media":  pools["media"][:10],
        "today":  today
    }

def build_daily(today, raw):
    return {
        "date": today,
        "source_note": "本日报内容由脚本自动抓取自下方公开渠道 RSS，每条均保留原始链接可溯源。「读懂这条」如为空，表示当天无人工解读，请点链接查看原文。",
        "overview": f"自动汇总 {today} AI 圈公开 RSS 源最新资讯，共抓取 {len(raw['global'])+len(raw['media'])} 条。详细解读、股市与人才板块由人工每日 17:00 后补充。",
        "sections": [
            {"title": "🌍 海外 AI 巨头与科技媒体", "items": raw["global"]},
            {"title": "🇨🇳 国内 AI 媒体精选",       "items": raw["media"]},
        ],
        "stock":  {"rows": [], "note": "今日股市板块待人工补充。"},
        "talent": {"rows": [], "keywords": "今日人才板块待人工补充。"},
        "takeaways": [
            f"本日共抓取 {len(raw['global'])+len(raw['media'])} 条 AI 资讯，详见上方板块。",
            "每条均带原始链接，点击「来源」可直达原文核实。",
            "股市方向、人才市场板块由人工每日 17:00 后追加更新。"
        ]
    }

def write_daily(today, data):
    path = os.path.join(DATA_DIR, f"{today}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log(f"已写入 {path}")

def update_index():
    files = [f for f in os.listdir(DATA_DIR) if re.match(r'^\d{4}-\d{2}-\d{2}\.json$', f)]
    dates = sorted([f[:-5] for f in files], reverse=True)
    idx = {"dates": dates, "last_update": datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M')}
    with open(os.path.join(DATA_DIR, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    log(f"index.json 已更新，共 {len(dates)} 期")

def git_push():
    import subprocess
    def run(cmd):
        log(f"  $ {cmd}")
        r = subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True)
        if r.stdout.strip(): log(r.stdout.strip())
        if r.returncode != 0:
            log(f"  ⚠ {r.stderr.strip()}")
            return False
        return True
    run("git add -A")
    msg = f"daily auto update {datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M')}"
    run(f'git commit -m "{msg}" || echo "nothing to commit"')
    run("GIT_TERMINAL_PROMPT=0 git push origin main")

def main():
    try:
        log("====== AI 早报自动更新开始 ======")
        raw = collect()
        today = raw["today"]
        # 如果今天已经有人工版本，则不要覆盖（人工 > 自动）
        path = os.path.join(DATA_DIR, f"{today}.json")
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                exist = json.load(f)
            # 如果存在的版本 stock/talent 非空，则视为人工已编辑过 → 不覆盖
            if exist.get('stock', {}).get('rows') or exist.get('talent', {}).get('rows'):
                log("今日已有人工编辑版，跳过抓取覆盖")
            else:
                data = build_daily(today, raw)
                write_daily(today, data)
        else:
            data = build_daily(today, raw)
            write_daily(today, data)
        update_index()
        git_push()
        log("====== 完成 ======")
    except Exception:
        log("脚本异常：\n" + traceback.format_exc())
        sys.exit(1)

if __name__ == '__main__':
    main()
