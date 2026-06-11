import json

with open('data/2026-06-11.json', encoding='utf-8') as f:
    t = json.load(f)
with open('data/2026-06-10.json', encoding='utf-8') as f:
    y = json.load(f)

# 从 6/10 继承真实的长期岗位池(B组)
long_pool = None
for g in y.get('talent', {}).get('groups', []):
    if '长期岗位池' in g.get('title', ''):
        long_pool = g
        break

groups = []
# A组：今日无招聘信号，诚实标注（绝不虚构）
groups.append({
    'title': '🅰️ 今日新闻中的真实招聘信号',
    'rows': [],
    'note': '今日（2026-06-11）AI 资讯以模型发布、技术与政策类为主，未出现明确的招聘/扩招/创业团队信号，故本组留空（绝不虚构）。求职可直接查看下方长期岗位池。'
})
# B组：真实长期岗位池
if long_pool:
    lp = dict(long_pool)
    lp['title'] = '🅱️ 长期岗位池（人工整理真实 JD，更新于 2026-06-09，行情变化不大时沿用）'
    groups.append(lp)

t['talent'] = {
    'groups': groups,
    'keywords': '今日招聘信号：当日新闻自动提取，今日无明确信号故留空；长期岗位池：人工整理真实 JD（WPS/叫叫/微思敦/快手等），均可点链接溯源。绝不虚构岗位/公司/薪资。'
}

with open('data/2026-06-11.json', 'w', encoding='utf-8') as f:
    json.dump(t, f, ensure_ascii=False, indent=2)

print('OK 6/11 人才板块已重建')
print('A组 rows:', len(groups[0]['rows']), '(今日无信号,诚实留空)')
print('B组:', groups[1]['title'] if len(groups) > 1 else '无', '| rows:', len(groups[1]['rows']) if len(groups) > 1 else 0)
