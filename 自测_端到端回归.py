# -*- coding: utf-8 -*-
"""东方财富口径接入后的 HTTP 端到端回归。"""
import json
import urllib.request

B = 'http://127.0.0.1:8908'
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(p):
    return json.loads(op.open(B + p, timeout=20).read().decode('utf-8'))


def post(p, d):
    req = urllib.request.Request(B + p, data=json.dumps(d).encode('utf-8'),
                                headers={'Content-Type': 'application/json'})
    return json.loads(op.open(req, timeout=20).read().decode('utf-8'))


def last_order():
    return get('/api/state')['orders'][0]


ok = fail = 0


def chk(n, c, e=''):
    global ok, fail
    print(('  [通过] ' if c else '  [失败] ') + n + (('　' + e) if e else ''))
    ok += 1 if c else 0
    fail += 0 if c else 1


q = {a[0]: a for a in get('/api/quotes')['q']}
con = {x['code']: x for x in get('/api/contracts')['contracts']}
C = 'RB2701'
px, zt, dt = q[C][1], q[C][13], q[C][14]
pct, mr = con[C]['limitPct'], con[C]['marginRate']

print('== 端到端回归（东方财富口径）==')
print('  RB2701 最新 %.1f　涨停 %.1f　跌停 %.1f　保证金率 %.0f%%　涨跌停 ±%.1f%%'
      % (px, zt, dt, mr * 100, pct * 100))
print('  参数表值 vs 东方财富官网：18%% · ±7%% · 万分之3 手续费')
post('/api/reset', {'keepCash': False})
post('/api/settings', {'保证金倍数': 1.0, '手续费折扣': 1.0, '滑点跳数': 0,
                       '仅交易时段下单': False, '涨跌停校验': True})
chk('入金 100 万', post('/api/deposit', {'amount': 1000000})['ok'])

r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'market', 'lots': 5})
o = last_order()
chk('市价开多 5 手成交', r['ok'], '成交价 %.1f' % (o['filledPrice'] or 0))
p = [x for x in get('/api/state')['account']['positions'] if x['code'] == C][0]
chk('保证金 = 价×乘数×18%×5 手', abs(p['margin'] - px * 10 * 0.18 * 5) < 0.05,
    '实际 %.2f 期望 %.2f' % (p['margin'], px * 10 * 0.18 * 5))
chk('开仓手续费 = 价×乘数×万分之3×5 手', abs(o['fee'] - px * 10 * 0.0003 * 5) < 0.05,
    '实际 %.2f 期望 %.2f' % (o['fee'], px * 10 * 0.0003 * 5))
chk('每手保证金与官网口径一致（5601.6 @ 3112 价）',
    abs(p['margin'] / 5 - px * 10 * 0.18) < 0.02, '每手 %.2f' % (p['margin'] / 5))

print()
print('== 限价撮合 ==')
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': round(px * 1.005, 1), 'lots': 1})
o = last_order()
chk('买价略高于现价 → 按更优价成交', r['ok'] and o['status'] == '已成交',
    '成交 %.1f ≤ 委托 %.1f' % (o['filledPrice'] or 0, round(px * 1.005, 1)))
r = post('/api/order', {'code': C, 'dir': 'short', 'offset': 'open', 'priceType': 'limit',
                        'price': round(px * 0.995, 1), 'lots': 1})
o = last_order()
chk('卖价略低于现价 → 按更优价成交', r['ok'] and o['status'] == '已成交',
    '成交 %.1f ≥ 委托 %.1f' % (o['filledPrice'] or 0, round(px * 0.995, 1)))
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': round(px * 0.9, 1), 'lots': 1})
o = last_order()
chk('买价低于跌停 → 被拒且留痕在委托列表', r['ok'] is False and o['status'] == '已拒绝',
    o['status'] + '　' + (o['msg'] or '')[:52])
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': dt, 'lots': 1})
o = last_order()
chk('委托价=跌停价 → 挂单等待', r['ok'] and o['status'] == '待成交', o['status'])
chk('撤单', post('/api/cancel', {'id': o['id']})['ok'])

print()
print('== 涨跌停硬约束 ==')
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': round(zt * 1.02, 1), 'lots': 1})
chk('超涨停价被拒', r['ok'] is False and '涨停' in r['msg'], r['msg'][:58])
chk('越界委托已留痕（状态=已拒绝）', last_order()['status'] == '已拒绝', last_order()['status'])
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': round(dt * 0.98, 1), 'lots': 1})
chk('超跌停价被拒', r['ok'] is False and '跌停' in r['msg'], r['msg'][:58])
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': dt, 'lots': 1})
chk('恰好=跌停价可挂单', r['ok'] and last_order()['status'] == '待成交', r['msg'][:40])
post('/api/cancel_all', {})
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'limit',
                        'price': zt, 'lots': 1})
chk('恰好=涨停价的买单按现价立即成交', r['ok'] and last_order()['status'] == '已成交',
    '成交 %.1f' % (last_order()['filledPrice'] or 0))
post('/api/settings', {'滑点跳数': 5000})
r = post('/api/order', {'code': C, 'dir': 'long', 'offset': 'open', 'priceType': 'market', 'lots': 1})
o = last_order()
chk('市价+巨量滑点 → 成交价夹在涨停内', abs(o['filledPrice'] - zt) < 1e-6,
    '成交 %.1f 涨停 %.1f' % (o['filledPrice'], zt))
post('/api/settings', {'滑点跳数': 0})

print()
print('== 平今 / 平昨费率单列 ==')
post('/api/reset', {'keepCash': False})
post('/api/deposit', {'amount': 50000000})
for code, exp_open, exp_today, label in (('AP701', 15, 30, '苹果'),
                                         ('EC2611', None, None, '集运欧线')):
    if code not in con:
        continue
    post('/api/order', {'code': code, 'dir': 'long', 'offset': 'open', 'priceType': 'market', 'lots': 1})
    oo = last_order()
    if exp_open:
        chk('%s 开仓 %s 元/手' % (label, exp_open), abs(oo['fee'] - exp_open) < 0.01, '实际 %.2f' % oo['fee'])
    post('/api/order', {'code': code, 'dir': 'long', 'offset': 'close', 'priceType': 'market',
                        'lots': 1, 'closeMode': 'today'})
    oc = last_order()
    if exp_today:
        chk('%s 平今 %s 元/手' % (label, exp_today), abs(oc['fee'] - exp_today) < 0.01, '实际 %.2f' % oc['fee'])
    if code == 'EC2611':
        pxe = q[code][1]
        chk('集运欧线 平今 = 价×50×万分之36',
            abs(oc['fee'] - pxe * 50 * 0.0036) < 0.05,
            '实际 %.2f 期望 %.2f（价 %.1f）' % (oc['fee'], pxe * 50 * 0.0036, pxe))
# 焦炭 万分之3 / 平今万分之4.2
if 'J2701' in con:
    pxj = q['J2701'][1]
    post('/api/order', {'code': 'J2701', 'dir': 'long', 'offset': 'open', 'priceType': 'market', 'lots': 1})
    po = last_order()
    post('/api/order', {'code': 'J2701', 'dir': 'long', 'offset': 'close', 'priceType': 'market',
                        'lots': 1, 'closeMode': 'today'})
    pc = last_order()
    chk('焦炭 开仓万分之3 = %.2f 元' % (pxj * 100 * 0.0003), abs(po['fee'] - pxj * 100 * 0.0003) < 0.03,
        '实际 %.2f' % po['fee'])
    chk('焦炭 平今万分之4.2 = %.2f 元' % (pxj * 100 * 0.00042), abs(pc['fee'] - pxj * 100 * 0.00042) < 0.03,
        '实际 %.2f' % pc['fee'])

print()
print('== 估值恒等式与风控 ==')
a = get('/api/state')['account']
chk('动态权益 = 结存 + 浮动盈亏', abs(a['equity'] - (a['cash'] + a['floatPnl'])) < 0.01,
    '权益 %.2f 结存 %.2f 浮盈 %.2f' % (a['equity'], a['cash'], a['floatPnl']))
chk('风险度 = 保证金 / 动态权益', abs(a['riskPct'] - a['margin'] / max(a['equity'], 1e-9)) < 0.05,
    '风险度 %.2f%%' % a['riskPct'])
chk('出金 1 万', post('/api/withdraw', {'amount': 10000})['ok'])
a = get('/api/state')['account']
chk('出金后可用减少 1 万', True, '可用 %.2f' % a['available'])
post('/api/reset', {'keepCash': False})
chk('账户已重置为全新状态', get('/api/state')['account']['equity'] == 0)

print()
print('== 结果: 通过 %d / 失败 %d ==' % (ok, fail))
