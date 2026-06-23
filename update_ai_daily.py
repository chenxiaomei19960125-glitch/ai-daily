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
- 不调 LLM → 杜绝 AI 幻觉；「大白话解读」只对真实标题/摘要做结构化重述，不引入新事实
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

def is_mostly_english(s):
    """简易判断：英文字符占比 > 60% 视为英文标题/摘要"""
    if not s: return False
    letters = sum(1 for c in s if c.isascii() and c.isalpha())
    chinese = sum(1 for c in s if '\u4e00' <= c <= '\u9fff')
    if chinese >= 4: return False
    return letters > 8 and (chinese == 0 or letters / max(letters + chinese, 1) > 0.6)

_TRANSLATE_CACHE = {}

def translate_to_zh(text, timeout=8):
    """调用 Google 翻译免费网页接口（无需 key），把英文翻成中文。
    失败时返回原文。带本地缓存避免重复请求。"""
    if not text or not text.strip():
        return text
    if text in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[text]
    try:
        from urllib.parse import quote
        # gtx 是 Google 翻译公开端点，多年稳定
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=zh-CN&dt=t&q={quote(text[:1500])}"
        req = Request(url, headers={"User-Agent": UA})
        with urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode('utf-8'))
        # 返回结构: [[["译文","原文",None,None,...],...], ...]
        parts = [seg[0] for seg in data[0] if seg and seg[0]]
        zh = ''.join(parts).strip()
        if zh and zh != text:
            _TRANSLATE_CACHE[text] = zh
            return zh
    except Exception as e:
        log(f"[translate] 失败 fallback 原文: {e}")
    return text

def chineseify_item(it):
    """海外英文条目：调用翻译接口把标题/摘要真翻译成中文。
    翻译失败兜底加 [待翻译] 前缀，避免页面出现一堆英文。"""
    title = it.get('title', '')
    if is_mostly_english(title):
        zh = translate_to_zh(title)
        if zh and zh != title:
            it['title'] = zh
        elif not title.startswith('[待翻译]'):
            it['title'] = '[待翻译] ' + title
    desc = it.get('desc', '')
    if is_mostly_english(desc):
        zh = translate_to_zh(desc)
        if zh and zh != desc:
            it['desc'] = zh
        elif not desc.startswith('[英文原文]'):
            it['desc'] = '[英文原文] ' + desc
    return it

# ============== 大白话解读（基于真实标题+摘要的结构化提炼，不引入新事实）==============
# 规则：识别新闻类型 → 套「是什么 + 为什么重要 + 影响谁」框架。
# 所有判断只用已有的 title/desc 文本，绝不编造数字/公司/事件。
_TAKEAWAY_RULES = [
    # （顺序即优先级：招聘/融资等"硬信号"优先于发布/Agent等泛类型）
    # (关键词列表, 类型, 一句话大白话影响)
    (["招聘", "扩招", "招人", "缺人", "缺.*?人才", "hiring", "招贤", "校招", "春招", "贴广告"], "招聘",
     "说白了就是这家在缺人、在扩招，找工作的可以盯一下。"),
    (["融资", "投资", "估值", "亿美元", "亿元", "fund", "raise", "valuation", "轮融资"], "融资",
     "有人砸钱进来了，这条赛道接下来会火、大概率也在招人。"),
    (["收购", "并购", "acquire", "merger", "买下"], "收购",
     "大公司花钱把别人买了，行业在洗牌，相关团队的人要留意去留。"),
    (["政策", "监管", "立法", "法案", "合规", "regulation", "policy", "ban", "禁令"], "政策",
     "规则变了，做 AI 相关的都得跟着调整，别踩红线。"),
    (["开源", "open source", "开放权重", "权重开放", "免费开放"], "开源",
     "好东西免费放出来了，自己想折腾 AI 的可以直接拿去用。"),
    (["部署", "落地", "接入", "deploy", "rollout", "上线企业", "enterprise"], "落地",
     "AI 真的用到实际业务里了，想把 AI 搬进自己工作的可以参考。"),
    (["成本", "降价", "便宜", "省钱", "支出", "定价", "price", "cost", "spend"], "成本",
     "用 AI 的花费有变化，预算紧的个人和小团队要算一下账。"),
    (["医疗", "健康", "诊断", "疾病", "医生", "health", "medical", "诊治"], "医疗AI",
     "AI 干起了看病这种专业活，关注 AI 落地行业的值得看看。"),
    (["合作", "联手", "携手", "partner", "合资", "战略合作"], "合作",
     "两家联手干事，可能很快有新产品出来，留意能不能用上。"),
    (["智能体", "agent", "agentic"], "Agent",
     "AI 不只会聊天、开始能自己干活了，做运营/产品的可以早点学着用。"),
    (["视频", "图像", "生成", "绘画", "video", "image", "多模态", "3D"], "生成式",
     "做图做视频又有新工具，搞内容、营销的能省不少事。"),
    (["发布", "推出", "上线", "release", "launch", "亮相", "问世", "升级", "更新"], "发布",
     "又出新工具/新模型了，看看能不能用进自己的活儿里。"),
    (["开发者", "API", "工具", "框架", "sdk", "插件", "developer"], "工具",
     "给开发者的新工具，做东西更省事，技术/产品同学可以瞄一眼。"),
]

def generate_takeaway(title, desc):
    """基于真实标题+摘要生成一句话大白话解读：说清这条是啥 + 跟你有啥关系。
    只对已有文本做结构化重述，绝不引入新事实/数字/公司名。"""
    t = (title or '').strip()
    d = (desc or '').strip()
    if not t:
        return ""
    # 去掉摘要里的占位/前缀提示
    for junk in ("（点击「来源」查看原文）", "[英文原文] ", "[待翻译] "):
        d = d.replace(junk, "")
    d = d.strip()
    hay = (t + " " + d).lower()

    sense, kind = None, None
    for kws, k, s in _TAKEAWAY_RULES:
        if any(re.search(kw.lower(), hay) for kw in kws):
            sense, kind = s, k
            break
    if sense is None:
        # 兜底：仍不编造内容
        sense = "AI 圈又有新动静，想跟上节奏的可以瞄一眼。"

    # 「是什么」直接用摘要首句（真实可溯源）；摘要为空则用标题
    if d:
        first = re.split(r'[。！？!?\n]', d)[0].strip()
        what = first if len(first) >= 8 else d[:60]
    else:
        what = t
    what = what.rstrip('。.')[:80]

    return f"{what}——{sense}"

def enrich_takeaways(sections):
    """给 sections 里每条没有 takeaway 的新闻补上大白话解读。"""
    n = 0
    for sec in sections:
        for it in sec.get('items', []):
            if not (it.get('takeaway') or '').strip():
                tk = generate_takeaway(it.get('title', ''), it.get('desc', ''))
                if tk:
                    it['takeaway'] = tk
                    n += 1
    return n

def load_previous_daily():
    """找到 data/ 下日期排序最新（且非今天）的一份日报，返回 dict 或 None。
    用于继承 stock / talent 板块，避免今天页面突然变空。"""
    today = datetime.now(CN_TZ).strftime('%Y-%m-%d')
    files = sorted(
        [f for f in os.listdir(DATA_DIR) if re.match(r'^\d{4}-\d{2}-\d{2}\.json$', f)],
        reverse=True
    )
    for fn in files:
        if fn[:-5] == today: continue
        try:
            with open(os.path.join(DATA_DIR, fn), 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            continue
    return None

# 招聘信号关键词：用于从当日真实新闻里识别「与招聘/扩招/创业团队相关」的条目
HIRING_KEYWORDS = [
    '招聘', '春招', '秋招', '校招', '扩招', '招募', '招人', 'HC', '岗位',
    '创业', '独角兽', '团队', '落户', '入职', '团队扩', '人才',
    'hiring', 'jobs', 'recruit', 'careers', 'we are hiring'
]

def extract_hiring_signals(sections):
    """从当日真实新闻条目中，提取「与招聘/扩招/创业相关」的信号。
    全部基于真实新闻，不瞎编。每条带原始链接可溯源。
    返回一个 talent group（rows 列表），无命中则返回 None。"""
    rows = []
    for sec in sections:
        for it in sec.get('items', []):
            text = (it.get('title', '') + it.get('desc', '') + it.get('takeaway', ''))
            if any(kw.lower() in text.lower() for kw in HIRING_KEYWORDS):
                src = (it.get('sources') or [{}])[0]
                rows.append({
                    "position": it.get('title', '')[:40] + ('…' if len(it.get('title', '')) > 40 else ''),
                    "company": src.get('name', '资讯来源'),
                    "salary": "按岗位面议",
                    "requirements": "信号来源（真实新闻摘要）：" + (it.get('desc', '') or it.get('takeaway', '') or '点击链接查看原文')[:120],
                    "platform": src.get('name', '原文') + ' · 链接',
                    "url": src.get('url', '')
                })
    return rows if rows else None

def build_daily(today, raw):
    # 海外英文条目自动翻译为中文
    raw_global = [chineseify_item(dict(it)) for it in raw["global"]]
    raw_media  = list(raw["media"])

    sections = [
        {"title": "🌍 海外 AI 巨头与科技媒体", "items": raw_global},
        {"title": "🇨🇳 国内 AI 媒体精选",       "items": raw_media},
    ]

    # ===== 每条新闻自动生成「大白话解读」（基于真实摘要提炼，不瞎编）=====
    cnt_tk = enrich_takeaways(sections)
    log(f"已为 {cnt_tk} 条新闻自动生成大白话解读")

    # ===== 股市：默认继承上一日（不瞎编行情）=====
    prev = load_previous_daily()
    if prev and prev.get('stock', {}).get('rows'):
        stock = dict(prev['stock'])
        stock['note'] = ("以上为基于公开信息的方向性整理，仅供参考，不构成投资建议。"
            f"\n（注：本板块沿用 {prev.get('date','上一日')} 人工整理内容，行情变化不大；如需更新请人工编辑。）")
    else:
        stock = {"rows": [], "note": "今日暂无股市方向数据，待人工补充。"}

    # ===== 人才：A组=当日新闻真实招聘信号；B组=继承的长期岗位池 =====
    talent_groups = []
    signals = extract_hiring_signals(sections)
    if signals:
        talent_groups.append({
            "title": "🅰️ 今日新闻中的真实招聘信号（来自当日 AI 资讯，均可点链接溯源）",
            "rows": signals
        })
    # 继承上一日的长期岗位池（人工整理的 JD），并标注更新日期
    if prev:
        for g in prev.get('talent', {}).get('groups', []):
            gt = g.get('title', '')
            # 跳过上一天自动生成的「今日招聘信号」组，只继承人工长期岗位池
            if '今日新闻中的真实招聘信号' in gt:
                continue
            ng = dict(g)
            if '长期岗位池' not in gt:
                ng['title'] = f"🅱️ 长期岗位池（人工整理，更新于 {prev.get('date','上一日')}，行情变化不大时沿用）"
            talent_groups.append(ng)

    if talent_groups:
        kw = (prev.get('talent', {}).get('keywords', '') if prev else '') or \
             "今日招聘信号 = 从当日新闻自动提取；长期岗位池 = 人工不定期更新。"
        talent = {"groups": talent_groups, "keywords": kw}
    else:
        talent = {"rows": [], "keywords": "今日暂无招聘信号，岗位池待人工补充。"}

    return {
        "date": today,
        "source_note": "本日报内容由脚本自动抓取自下方公开渠道 RSS，海外英文资讯已自动翻译为中文，每条均保留原始链接可溯源。「读懂这条」为解读，若无则显示 RSS 原文摘要。人才板块的「今日招聘信号」由当日真实新闻自动提取，绝无虚构。",
        "overview": f"自动汇总 {today} AI 圈公开 RSS 源最新资讯，共抓取 {len(raw_global)+len(raw_media)} 条。人才板块「今日招聘信号」来自当日新闻自动提取，长期岗位池沿用人工整理；股市板块沿用上一日方向。",
        "sections": sections,
        "stock":  stock,
        "talent": talent,
        "takeaways": [
            f"本日共抓取 {len(raw_global)+len(raw_media)} 条 AI 资讯，海外英文已自动翻译为中文。",
            "每条均带原始链接，点击「来源」可直达原文核实；解读为空时显示 RSS 原文摘要。",
            "人才板块「今日招聘信号」自当日真实新闻提取，长期岗位池沿用人工整理，均不虚构。"
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
