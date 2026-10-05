/* =====================================================================
   国内期货模拟交易终端 · 行情 / 技术指标 / 策略 / 回测 / 复盘
   —— 与 交易面板.html 同页，复用其全局：$ el fmt fmtInt num money signCls
      signed api S toast openMask closeMask selectContract
   ===================================================================== */
/* global echarts, api, S, $, el, fmt, fmtInt, num, money, signCls, signed, toast, openMask, closeMask, selectContract */
(function () {
  'use strict';

  const UP = '#d8342b', DOWN = '#0a9d5c', FLAT = '#6b7a8d';
  const PAL = ['#2563eb', '#e8a33d', '#7c3aed', '#0891b2', '#db2777',
    '#65a30d', '#b45309', '#0f766e'];
  const PERIOD_LABEL = { m1: '1分', m3: '3分', m5: '5分', m15: '15分', m30: '30分',
    m60: '60分', m120: '2时', m240: '4时', day: '日K', week: '周K', month: '月K' };

  const Q = {
    cfg: null,
    page: 'market',
    // ---- 行情 ----
    mkMode: 'main', mkSector: '', mkKw: '', mkSel: '', mkRows: [],
    period: 'day', view: 'kline',
    mainOn: ['MA'], subOn: ['VOL', 'MACD'],
    mainArgs: { MA: '5,10,20,60', EMA: '12,26', BOLL: '20,2', SAR: '4,2,2' },
    subArgs: { VOL: '5,10,20', MACD: '12,26,9', KDJ: '9,3,3', RSI: '6,12,24',
      WR: '10,6', BIAS: '6,12,24', OBV: '30', DMI: '14,6', CCI: '14', ATR: '14' },
    kchart: null, fsChart: null,
    signalOn: false, signalStrat: '', marks: [],
    // ---- 策略 ----
    strategies: [], templates: [], directions: ['双向', '仅做多', '仅做空'],
    sectors: [], curStrat: '', editing: null,
    // ---- 回测 ----
    bt: null, btK: null, btE: null, btMm: null,
    // ---- 复盘 ----
    rp: null, rpChart: null, rpEq: null, rpTimer: null, rpSpeed: 220, rpMm: null,
    rpExpandAll: false, rpView: 'group',
  };

  const $id = (s) => document.getElementById(s);
  const esc = (s) => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

  function cls(v) { return v > 0 ? 'up' : (v < 0 ? 'down' : 'flat'); }
  function pct(v, d = 2) {
    return (v === null || v === undefined || isNaN(v)) ? '--' : (v > 0 ? '+' : '') + Number(v).toFixed(d) + '%';
  }
  function px(v) {
    if (v === null || v === undefined || isNaN(v)) return '--';
    const a = Math.abs(Number(v));
    return Number(v).toLocaleString('zh-CN', {
      minimumFractionDigits: a >= 1000 ? 1 : (a >= 10 ? 1 : 2),
      maximumFractionDigits: a >= 1000 ? 1 : (a >= 10 ? 2 : 3),
    });
  }
  function dtLabel(d) {
    const s = String(d || '');
    if (s.length >= 16) return s.slice(5, 16);
    return s.slice(5) || s;
  }
  function chooseColor(v) { return v > 0 ? UP : (v < 0 ? DOWN : FLAT); }

  /* ==================================================================
     页面切换
     ================================================================== */
  function gotoPage(page) {
    Q.page = page;
    document.querySelectorAll('#topNav button').forEach(b =>
      b.classList.toggle('on', b.dataset.page === page));
    document.querySelectorAll('.page').forEach(p =>
      p.classList.toggle('on', p.id === 'page-' + page));
    if (page === 'market') setTimeout(() => { resizeCharts(); }, 60);
    if (page === 'backtest') setTimeout(() => { resizeCharts(); }, 60);
    if (page === 'replay') setTimeout(() => { resizeCharts(); }, 60);
    if (page === 'trade') setTimeout(() => { if (S.curveChart) S.curveChart.resize(); }, 60);
    if (page === 'account') setTimeout(() => { accLoad(); }, 40);
  }
  function resizeCharts() {
    [Q.kchart, Q.fsChart, Q.btK, Q.btE, Q.rpChart, Q.rpEq].forEach(c => { try { c && c.resize(); } catch (e) { } });
  }
  document.addEventListener('click', e => {
    const b = e.target.closest('#topNav button');
    if (b) gotoPage(b.dataset.page);
  });
  window.addEventListener('resize', () => resizeCharts());

  /* ==================================================================
     一、行情 · 技术指标
     ================================================================== */
  function allVarieties() {
    const set = new Set();
    (S.contracts || []).forEach(c => c.variety && set.add(c.variety));
    Object.keys((S.cfg && S.cfg['品种参数']) || {}).forEach(v => set.add(v));
    return Array.from(set);
  }
  function mainContractOf(variety) {
    let best = null, score = -1;
    (S.contracts || []).forEach(c => {
      if (c.variety !== variety) return;
      if (c.kind && c.kind !== 'real') return;
      if (/主连|连续|指数|加权|月均/.test(c.name || '')) return;
      const q = S.quote[c.code] || [];
      const s = (q[5] || 0) * 1000 + (q[4] || 0);
      if (s > score) { score = s; best = c; }
    });
    return best;
  }
  const SECTOR_OF = (v) => {
    const c = (S.contracts || []).find(x => x.variety === v);
    return (c && c.sector) || '其他';
  };
  const NAME_OF = (v) => {
    const p = (S.cfg && S.cfg['品种参数'] || {})[v];
    if (p && p['名称']) return p['名称'];
    const c = (S.contracts || []).find(x => x.variety === v);
    return (c && c.varietyName) || v;
  };

  function buildMarketList() {
    const kw = Q.mkKw.trim().toUpperCase();
    let rows = [];
    if (Q.mkMode === 'real') {
      rows = (S.contracts || []).map(c => {
        const q = S.quote[c.code] || [];
        return { code: c.code, name: c.name, variety: c.variety, kind: 'real',
          p: q[1], zdf: q[2], vol: q[4], sector: c.sector || '其他' };
      });
    } else if (Q.mkMode === 'hold') {
      const ps = (S.state && S.state.account && S.state.account.positions) || [];
      rows = ps.map(p => {
        const mc = mainContractOf(p.variety) || {};
        const q = S.quote[p.code] || [];
        return { code: p.code, name: p.name, variety: p.variety, kind: 'pos',
          p: q[1], zdf: q[2], vol: q[4], sector: SECTOR_OF(p.variety),
          lots: p.lots, dir: p.dir, floatPnl: p.floatPnl };
      });
    } else {
      rows = allVarieties().map(v => {
        const mc = mainContractOf(v);
        const q = (mc && S.quote[mc.code]) || [];
        return { code: v + '0', name: NAME_OF(v) + '·主连', variety: v,
          kind: 'main', p: q[1], zdf: q[2], vol: q[4], sector: SECTOR_OF(v),
          real: mc && mc.code };
      });
    }
    if (Q.mkSector) rows = rows.filter(r => r.sector === Q.mkSector);
    if (kw) {
      rows = rows.filter(r => (r.code + ' ' + r.name + ' ' + r.variety)
        .toUpperCase().indexOf(kw) >= 0);
    }
    if (Q.mkMode === 'real') rows.sort((a, b) => (b.vol || 0) - (a.vol || 0));
    else rows.sort((a, b) => (b.vol || 0) - (a.vol || 0));
    Q.mkRows = rows.slice(0, 400);
  }

  function liveOf(r) {
    if (!r) return [];
    const k = (r.kind === 'main' && r.real) ? r.real : r.code;
    return S.quote[k] || S.quote[r.code] || [];
  }

  function updateMarketCells() {
    const tb = $id('mkBody');
    if (!tb) return;
    const rows = tb.querySelectorAll('tr[data-code]');
    rows.forEach(tr => {
      const r = Q.mkRows.find(x => x.code === tr.dataset.code);
      if (!r) return;
      const q = liveOf(r);
      if (!q.length) return;
      const c = tr.children;
      if (c.length < 4) return;
      c[2].textContent = px(q[1]);
      c[3].textContent = pct(q[2]);
      c[3].className = 'num ' + cls(q[2]);
      r.p = q[1]; r.zdf = q[2];
    });
  }

  let lastListSig = '';

  function renderMarketList(force) {
    buildMarketList();
    const tb = $id('mkBody');
    if (!tb) return;
    const sig = [Q.mkMode, Q.mkSector, Q.mkKw, Q.mkRows.length,
      Q.mkRows.map(r => r.code).join(',')].join('|');
    if (!force && sig === lastListSig && tb.querySelector('tr[data-code]')) {
      updateMarketCells();
      return;
    }
    lastListSig = sig;
    if (!Q.mkRows.length) {
      tb.innerHTML = '<tr><td colspan="4" class="empty">没有匹配的合约</td></tr>';
      return;
    }
    if (!Q.mkSel || !Q.mkRows.some(r => r.code === Q.mkSel)) Q.mkSel = Q.mkRows[0].code;
    tb.innerHTML = Q.mkRows.map(r =>
      '<tr data-code="' + esc(r.code) + '" class="' + (r.code === Q.mkSel ? 'on' : '') + '">' +
      '<td class="l"><b>' + esc(r.code) + '</b></td>' +
      '<td class="l">' + esc(r.name) + '</td>' +
      '<td class="num">' + px(r.p) + '</td>' +
      '<td class="num ' + cls(r.zdf) + '">' + pct(r.zdf) + '</td></tr>').join('');
    updateMarketCells();
  }

  function renderSeg() {
    const ps = $id('periodSeg');
    if (ps && !ps.dataset.done) {
      const list = (Q.cfg && Q.cfg['周期列表']) || Object.keys(PERIOD_LABEL)
        .map(k => ({ key: k, label: PERIOD_LABEL[k] }));
      ps.innerHTML = list.map(p => '<button data-p="' + p.key + '"' +
        (p.key === Q.period ? ' class="on"' : '') + '>' + p.label + '</button>').join('') +
        '<button data-p="fs"' + (Q.view === 'fs' ? ' class="on"' : '') + '>分时</button>';
      ps.dataset.done = '1';
      ps.addEventListener('click', e => {
        const b = e.target.closest('button'); if (!b) return;
        ps.querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
        if (b.dataset.p === 'fs') { Q.view = 'fs'; loadChart(); }
        else { Q.view = 'kline'; Q.period = b.dataset.p; loadChart(); }
      });
    }
  }

  function renderIndPickers() {
    const reg = (Q.cfg && Q.cfg['指标'] && Q.cfg['指标']['注册表']) || {};
    const mk = (box, list, on, args, max) => {
      if (!box) return;
      box.innerHTML = list.map(nm => {
        const isOn = on.indexOf(nm) >= 0;
        return '<span class="indchip' + (isOn ? ' on' : '') + '" data-nm="' + nm + '">' +
          '<span>' + nm + '</span>' +
          (isOn ? '<input data-arg="' + nm + '" value="' + esc(args[nm] || '') + '" ' +
            'title="' + esc((reg[nm] && reg[nm].args) || '') + '">' : '') +
          '</span>';
      }).join('') + (max ? '<span class="mini" style="margin-left:6px">最多 ' + max + ' 个副图</span>' : '');
    };
    mk($id('mainPick'), (Q.cfg && Q.cfg['指标'] && Q.cfg['指标']['主图列表']) || ['MA'], Q.mainOn, Q.mainArgs, 0);
    mk($id('subPick'), (Q.cfg && Q.cfg['指标'] && Q.cfg['指标']['副图列表']) || ['MACD'], Q.subOn, Q.subArgs, 3);

    const click = (box, on, args, max) => {
      box.onclick = (e) => {
        const inp = e.target.closest('input[data-arg]');
        if (inp) { args[inp.dataset.arg] = inp.value; return; }
        const chip = e.target.closest('.indchip'); if (!chip) return;
        const nm = chip.dataset.nm;
        const i = on.indexOf(nm);
        if (i >= 0) on.splice(i, 1);
        else { if (max && on.length >= max) { toast('副图最多选 ' + max + ' 个', 'warn'); return; } on.push(nm); }
        renderIndPickers(); loadChart();
      };
      box.oninput = (e) => {
        const inp = e.target.closest('input[data-arg]');
        if (inp) args[inp.dataset.arg] = inp.value;
      };
    };
    click($id('mainPick'), Q.mainOn, Q.mainArgs, 0);
    click($id('subPick'), Q.subOn, Q.subArgs, 3);
  }

  function mainSpecs() {
    return Q.mainOn.map(n => n + '(' + (Q.mainArgs[n] || '') + ')');
  }
  function subSpecs() {
    return Q.subOn.map(n => n + '(' + (Q.subArgs[n] || '') + ')');
  }

  async function loadChart() {
    const code = Q.mkSel;
    if (!code) return;
    const t = $id('mkTitle'), p = $id('mkPrice'), c = $id('mkChg'), m = $id('mkMeta');
    const row = Q.mkRows.find(x => x.code === code) || {};
    t.textContent = code + ' ' + (row.name || '');
    const q = (S.quote[row.real || code] || S.quote[code] || []);
    if (q.length) {
      p.textContent = px(q[1]);
      p.className = 'bigpx ' + cls(q[2]);
      c.textContent = pct(q[2]) + '  ' + (q[3] > 0 ? '+' : '') + fmt(q[3], 1);
      c.className = 'chg ' + cls(q[2]);
    }
    m.textContent = '数据源：新浪财经 · 点击左侧列表切换合约';

    if (Q.view === 'fs') {
      Q.kchart && Q.kchart.clear();
      try {
        const d = await api('/api/timeshare?code=' + encodeURIComponent(code) +
          '&variety=' + encodeURIComponent(row.variety || ''));
        renderTimeshare(d);
      } catch (e) { $id('mkNote').textContent = '分时加载失败：' + e.message; }
      return;
    }
    Q.fsChart && Q.fsChart.clear();
    try {
      const url = '/api/kline?code=' + encodeURIComponent(code) +
        '&period=' + Q.period + '&limit=520' +
        '&main=' + encodeURIComponent(mainSpecs().join(',')) +
        '&sub=' + encodeURIComponent(subSpecs().join(','));
      const d = await api(url);
      if (!d.ok) { $id('mkNote').textContent = d.msg || '加载失败'; return; }
      let marks = [];
      if (Q.signalOn && Q.signalStrat) {
        const sg = await api('/api/strategy/signals?id=' + encodeURIComponent(Q.signalStrat) +
          '&code=' + encodeURIComponent(code) + '&period=' + Q.period);
        marks = (sg && sg.marks) || [];
      }
      Q.marks = marks;
      renderKline(d, marks);
      const note = [];
      note.push('共 ' + d.count + ' 根（数据源共 ' + d.total + ' 根）');
      if (d.fallback) note.push('⚠ 该具体合约无历史数据，已回退「主力连续 ' + d.symbol + '」');
      if (d.stale) note.push('⚠ 联网失败，数据来自本地缓存');
      if (marks.length) note.push('叠加信号 ' + marks.length + ' 个');
      note.push('数据源：' + (d.source || '') + '，抓取于 ' + (d.fetchedAt || ''));
      $id('mkNote').textContent = note.join('　|　');
    } catch (e) { $id('mkNote').textContent = 'K 线加载失败：' + e.message; }
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return 'rgba(' + ((n >> 16) & 255) + ',' + ((n >> 8) & 255) + ',' + (n & 255) + ',' + a + ')';
  }

  function markPoints(marks, bars, byDate, entriesOnly) {
    const idxOf = {};
    if (byDate) bars.forEach((b, i) => { idxOf[b.d] = i; });
    return (marks || []).map(m => {
      if (entriesOnly && m.action !== '开') return null;
      const i = byDate ? idxOf[m.d] : m.i;
      if (i === undefined || i === null || i < 0 || i >= bars.length) return null;
      const isOpen = m.action === '开';
      const long = m.side === '多';
      const col = long ? UP : DOWN;
      const txt = isOpen ? (long ? 'B' : 'S') : '平';
      return {
        name: m.action + m.side, coord: [i, m.price], value: m.price,
        symbol: isOpen ? 'pin' : 'circle', symbolSize: isOpen ? 17 : 9,
        label: { formatter: txt, fontSize: 9, color: '#fff', offset: isOpen ? [0, -2] : 0 },
        itemStyle: { color: isOpen ? col : '#fff', borderColor: col, borderWidth: 1.6, opacity: isOpen ? 1 : .8 },
        _reason: m.reason, _d: m.d,
      };
    }).filter(Boolean);
  }

  function renderKline(d, marks) {
    const box = $id('kChart');
    if (!box) return;
    if (!Q.kchart) Q.kchart = echarts.init(box, null, { renderer: 'canvas' });
    const bars = d.bars || [];
    if (!bars.length) { Q.kchart.clear(); return; }
    const cats = bars.map(b => dtLabel(b.d));
    const ohlc = bars.map(b => [b.o, b.c, b.l, b.h]);
    const main = d.main || [], subs = d.sub || [];
    const H = box.clientHeight || 660;
    const subsN = subs.length;
    const subH = 100, gap = 24, topPad = 30, botPad = 54;
    const mainH = Math.max(170, H - topPad - botPad - subsN * (subH + gap));
    const grids = [{ left: 62, right: 22, top: topPad, height: mainH }];
    let y = topPad + mainH + gap;
    subs.forEach(() => { grids.push({ left: 62, right: 22, top: y, height: subH }); y += subH + gap; });

    const xAxes = grids.map((g, i) => ({
      type: 'category', gridIndex: i, data: cats, boundaryGap: true,
      axisLine: { lineStyle: { color: '#dfe6ef' } },
      axisTick: { show: false },
      axisLabel: { show: i === grids.length - 1, fontSize: 10.5, color: '#8b9aad' },
      splitLine: { show: false },
      axisPointer: { label: { show: false } },
    }));
    const yAxes = grids.map((g, i) => ({
      gridIndex: i, scale: true, position: 'left',
      axisLine: { show: false }, axisTick: { show: false },
      axisLabel: { fontSize: 10.5, color: '#8b9aad', margin: 6 },
      splitLine: { lineStyle: { color: '#eef2f7' } },
      splitNumber: i === 0 ? 5 : 3,
    }));

    const series = [{
      name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: ohlc,
      itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      markPoint: { data: markPoints(marks, bars, true, true), symbolOffset: [0, 0] },
    }];

    main.forEach((it, mi) => {
      it.lines.forEach(ln => {
        series.push({
          name: ln.label, type: ln.type === 'scatter' ? 'scatter' : 'line',
          xAxisIndex: 0, yAxisIndex: 0,
          data: bars.map((b, i) => {
            const v = ln.data[i];
            return v === null ? null : (ln.type === 'scatter' ? [i, v] : v);
          }),
          symbol: 'none', symbolSize: ln.type === 'scatter' ? 3 : 1,
          lineStyle: { width: 1.2, color: ln.color },
          itemStyle: { color: ln.color }, smooth: false, connectNulls: false,
          tooltip: { show: false },
        });
      });
    });

    subs.forEach((it, si) => {
      const gi = si + 1;
      if (it.bars) {
        const isVol = it.name === 'VOL';
        series.push({
          name: it.bars.label + '·' + it.name, type: 'bar', xAxisIndex: gi, yAxisIndex: gi,
          data: it.bars.data.map((v, i) => {
            if (v === null) return null;
            let col;
            if (isVol) col = (bars[i].c >= bars[i].o) ? hexA(UP, .85) : hexA(DOWN, .85);
            else col = v >= 0 ? UP : DOWN;
            return { value: v, itemStyle: { color: col } };
          }),
          barWidth: '62%',
        });
      }
      (it.lines || []).forEach(ln => {
        series.push({
          name: ln.label + '·' + it.name, type: 'line',
          xAxisIndex: gi, yAxisIndex: gi, data: ln.data,
          symbol: 'none', lineStyle: { width: 1.2, color: ln.color },
          itemStyle: { color: ln.color }, connectNulls: false,
          markLine: (it.guides && it.guides.length && ln === it.lines[0]) ? {
            silent: true, symbol: 'none',
            lineStyle: { color: '#c9d4e3', type: 'dashed', width: 1 },
            label: { show: false },
            data: it.guides.map(g => ({ yAxis: g })),
          } : undefined,
        });
      });
    });

    const legendData = series.map(s => s.name);
    Q.kchart.setOption({
      animation: false,
      backgroundColor: '#fff',
      legend: {
        top: 2, left: 60, type: 'scroll', itemWidth: 14, itemHeight: 8,
        textStyle: { fontSize: 11, color: '#5b6b80' }, data: legendData,
      },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
        backgroundColor: 'rgba(255,255,255,.97)', borderColor: '#dfe6ef',
        textStyle: { color: '#1b2432', fontSize: 11.5 },
        formatter: function (ps) {
          if (!ps || !ps.length) return '';
          const i = ps[0].dataIndex, b = bars[i];
          const mk = markPoints(marks, bars, true).filter(m => m.coord[0] === i);
          let h = '<b>' + b.d + '</b><br>开 ' + px(b.o) + '　高 ' + px(b.h) +
            '<br>低 ' + px(b.l) + '　收 ' + px(b.c) +
            '<br>量 ' + fmtInt(b.v) + (b.p ? '　持仓 ' + fmtInt(b.p) : '');
          if (b.s !== null && b.s !== undefined) h += '<br>结算 ' + px(b.s);
          ps.forEach(p => {
            if (p.seriesName === 'K线') return;
            const v = Array.isArray(p.value) ? p.value[1] : p.value;
            if (v === null || v === undefined) return;
            h += '<br>' + p.marker + p.seriesName + ' ' + fmt(v, 3);
          });
          mk.forEach(m => { h += '<br><span style="color:#b06a05">◆ ' + esc(m._reason) + '</span>'; });
          return h;
        },
      },
      axisPointer: { link: [{ xAxisIndex: 'all' }] },
      grid: grids, xAxis: xAxes, yAxis: yAxes, series: series,
      dataZoom: [
        { type: 'inside', xAxisIndex: xAxes.map((_, i) => i), start: 55, end: 100 },
        {
          type: 'slider', xAxisIndex: xAxes.map((_, i) => i),
          bottom: 8, height: 20, start: 55, end: 100,
          borderColor: '#dfe6ef', fillerColor: 'rgba(37,99,235,.10)',
          textStyle: { fontSize: 10, color: '#8b9aad' },
        },
      ],
    }, true);
  }

  function renderTimeshare(d) {
    const box = $id('kChart');
    if (!Q.kchart) Q.kchart = echarts.init(box, null, { renderer: 'canvas' });
    const pts = d.points || [];
    if (!pts.length) { Q.kchart.clear(); $id('mkNote').textContent = '暂无分时数据'; return; }
    const cats = pts.map(p => p.d);
    const line = pts.map(p => p.c);
    const avg = pts.map(p => p.avg);
    const vols = pts.map((p, i) => {
      const prev = i ? pts[i - 1].c : p.c;
      return { value: p.v || 0, itemStyle: { color: p.c >= prev ? hexA(UP, .8) : hexA(DOWN, .8) } };
    });
    const ref = d.preClose;
    const H = box.clientHeight || 660;
    const mainH = H - 30 - 54 - 100 - 24;
    Q.kchart.setOption({
      animation: false, backgroundColor: '#fff',
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(255,255,255,.97)', borderColor: '#dfe6ef',
        textStyle: { color: '#1b2432', fontSize: 11.5 },
        formatter: function (ps) {
          if (!ps || !ps.length) return '';
          const i = ps[0].dataIndex, p = pts[i];
          const pctv = ref ? (p.c - ref) / ref * 100 : 0;
          return '<b>' + p.d + '</b><br>价格 ' + px(p.c) +
            '　<span style="color:' + chooseColor(pctv) + '">' + pct(pctv) + '</span>' +
            '<br>均价 ' + px(p.avg) + '<br>成交量 ' + fmtInt(p.v) +
            (p.oi ? '<br>持仓 ' + fmtInt(p.oi) : '');
        },
      },
      grid: [
        { left: 62, right: 22, top: 26, height: Math.max(150, mainH) },
        { left: 62, right: 22, top: 26 + Math.max(150, mainH) + 24, height: 100 },
      ],
      xAxis: [
        { type: 'category', gridIndex: 0, data: cats, boundaryGap: false, axisLabel: { show: false }, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe6ef' } } },
        { type: 'category', gridIndex: 1, data: cats, boundaryGap: false, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe6ef' } }, axisLabel: { fontSize: 10.5, color: '#8b9aad', interval: Math.floor(cats.length / 8) } },
      ],
      yAxis: [
        { gridIndex: 0, scale: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10.5, color: '#8b9aad' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
        { gridIndex: 1, scale: true, splitNumber: 2, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10.5, color: '#8b9aad', formatter: v => fmtInt(v) }, splitLine: { show: false } },
      ],
      series: [
        {
          name: '价格', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: line,
          symbol: 'none', lineStyle: { width: 1.3, color: '#2563eb' }, areaStyle: { color: 'rgba(37,99,235,.07)' },
          markLine: ref ? {
            silent: true, symbol: 'none',
            lineStyle: { color: '#c9d4e3', type: 'dashed' },
            label: { formatter: '昨结 ' + px(ref), fontSize: 10, color: '#8b9aad' },
            data: [{ yAxis: ref }],
          } : undefined,
        },
        { name: '均价', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: avg, symbol: 'none', lineStyle: { width: 1.1, color: '#e8a33d' } },
        { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vols, barWidth: '68%' },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }],
    }, true);
    $id('mkNote').textContent = '分时数据 ' + pts.length + ' 点，交易日 ' +
      (d.date || '') + '，昨结 ' + px(ref) + '，最高 ' + px(d.high) + '，最低 ' + px(d.low) +
      '　|　数据源：' + (d.source || '');
  }

  function bindMarket() {
    $id('mkSearch').addEventListener('input', e => { Q.mkKw = e.target.value; renderMarketList(); });
    $id('mkMode').addEventListener('click', e => {
      const b = e.target.closest('button'); if (!b) return;
      $id('mkMode').querySelectorAll('button').forEach(x => x.classList.toggle('on', x === b));
      Q.mkMode = b.dataset.mode; Q.mkSel = ''; renderMarketList();
      $id('mkModeDesc').textContent = { main: '主力连续', real: '具体合约', hold: '我的持仓' }[Q.mkMode];
      const f = Q.mkRows[0];
      if (f) { Q.mkSel = f.code; renderMarketList(); loadChart(); }
    });
    $id('mkBody').addEventListener('click', e => {
      const tr = e.target.closest('tr'); if (!tr) return;
      Q.mkSel = tr.dataset.code;
      $id('mkBody').querySelectorAll('tr').forEach(x => x.classList.toggle('on', x === tr));
      loadChart();
    });
    $id('btnLoadChart').addEventListener('click', loadChart);
    $id('btnChartToTrade').addEventListener('click', () => {
      const r = Q.mkRows.find(x => x.code === Q.mkSel);
      const code = (r && (r.real || (r.kind === 'main' ? '' : r.code))) || Q.mkSel;
      if (!code) { toast('该品种没有可交易的具体合约', 'warn'); return; }
      gotoPage('trade');
      if (typeof selectContract === 'function') selectContract(code);
      const inp = $id('qSearch'); if (inp) { inp.value = code; inp.dispatchEvent(new Event('input')); }
    });
    $id('ckSignal').addEventListener('change', e => { Q.signalOn = e.target.checked; loadChart(); });
    $id('signalStrategy').addEventListener('change', e => { Q.signalStrat = e.target.value; if (Q.signalOn) loadChart(); });
    $id('btnIndLib').addEventListener('click', showIndLib);
    renderSeg();
    renderIndPickers();
  }

  function showIndLib() {
    const reg = (Q.cfg && Q.cfg['指标'] && Q.cfg['指标']['注册表']) || {};
    const order = ['MA', 'EMA', 'BOLL', 'SAR', 'VOL', 'MACD', 'KDJ', 'RSI', 'WR',
      'BIAS', 'OBV', 'DMI', 'CCI', 'ATR'];
    const items = order.filter(k => reg[k]).map(k => {
      const r = reg[k];
      return '<div class="it"><b>' + k + '</b> <span class="k">' +
        (r.kind === 'main' ? '主图叠加' : '副图') + '　' + esc(r.args || '') + '</span>' +
        '<div class="d">' + esc(r.desc || '') + '<br>默认参数：' +
        (r.default || []).join(',') + '</div></div>';
    }).join('');
    let mask = $id('maskIndLib');
    if (!mask) {
      mask = document.createElement('div');
      mask.className = 'mask'; mask.id = 'maskIndLib';
      mask.innerHTML = '<div class="modal" style="max-width:760px">' +
        '<h3>技术指标库</h3>' +
        '<div class="mini" style="margin-bottom:10px">主图指标叠加在 K 线上；副图指标独立面板（最多同时显示 3 个）。参数按指标定义顺序用英文逗号分隔，例如 MA 的 <b>5,10,20,60</b>。</div>' +
        '<div class="indlib" id="indLibBody"></div>' +
        '<div class="acts" style="margin-top:14px"><button class="btn primary" id="indLibOk">知道了</button></div></div>';
      document.body.appendChild(mask);
      mask.addEventListener('click', e => { if (e.target === mask) closeMask('maskIndLib'); });
    }
    $id('indLibBody').innerHTML = items;
    $id('indLibOk').onclick = () => closeMask('maskIndLib');
    openMask('maskIndLib');
  }

  /* ==================================================================
     〇、资金管理与仓位管理（策略 / 回测 / 复盘 共用）
     ================================================================== */
  function mmCfg() {
    return (Q.cfg && Q.cfg['资金管理']) || { 分组: [], 预设: [], 默认: {} };
  }

  /** 渲染一整块资金管理表单。pfx 用于 id 前缀，mm 为当前值 */
  function mmBlockHTML(pfx, mm) {
    const G = mmCfg();
    const cur = Object.assign({}, G['默认'] || {}, mm || {});
    const presets = G['预设'] || [];
    const h = [];
    h.push('<div class="sec-h">' + (pfx === 'st' ? '④ ' : '') +
      '资金管理与仓位管理' +
      '<span class="mini">策略只决定方向，这里决定「做多少手 · 错了在哪退出 · 什么时候不许做」</span></div>');
    h.push('<div class="mm-presets">' +
      presets.map(p => '<button type="button" class="btn tiny" data-mmpreset="' +
        esc(p.key) + '" title="' + esc(p.desc) + '">' + esc(p.name) + '</button>').join('') +
      '<span class="mini">一键套用科学预设</span></div>');
    h.push('<div class="mm-sum" id="' + pfx + 'MmSum"></div>');
    (G['分组'] || []).forEach((g, gi) => {
      h.push('<details class="mm-group"' + (gi < 2 ? ' open' : '') + '>' +
        '<summary>' + esc(g.title) + '</summary><div class="mm-grid">');
      (g.fields || []).forEach(f => {
        h.push(mmFieldHTML(pfx, f, cur[f.k]));
      });
      h.push('</div></details>');
    });
    return h.join('');
  }

  function mmFieldHTML(pfx, f, v) {
    const id = pfx + 'mm_' + f.k;
    let h = '<div class="fi' + (f.type === 'check' ? ' mm-ck' : '') + '"><label>' +
      esc(f.label || f.k) + '</label>';
    if (f.type === 'select') {
      const opts = f.options || [], keys = f.keys || [];
      h += '<select id="' + id + '" data-mmk="' + esc(f.k) + '">' +
        opts.map((o, i) => {
          const val = keys[i] !== undefined ? o : o;   // 保存中文标签
          const sel = String(v) === String(val) || String(v) === String(keys[i]) ? ' selected' : '';
          return '<option value="' + esc(val) + '"' + sel + '>' + esc(o) + '</option>';
        }).join('') + '</select>';
    } else if (f.type === 'check') {
      h += '<input id="' + id + '" data-mmk="' + esc(f.k) + '" type="checkbox"' +
        (v === false || v === 'false' ? '' : ' checked') + '>';
    } else {
      h += '<input id="' + id + '" data-mmk="' + esc(f.k) + '" type="number" step="' +
        (f.step || 'any') + '" min="0" value="' + esc(v === undefined ? '' : v) + '">';
    }
    h += f.hint ? '<div class="hint">' + esc(f.hint) + '</div>' : '';
    return h + '</div>';
  }

  /** 从 DOM 收集资金管理配置 */
  function mmCollect(pfx) {
    const G = mmCfg();
    const out = Object.assign({}, G['默认'] || {});
    (G['分组'] || []).forEach(g => (g.fields || []).forEach(f => {
      const e = $id(pfx + 'mm_' + f.k);
      if (!e) return;
      if (f.type === 'check') out[f.k] = !!e.checked;
      else if (f.type === 'select') out[f.k] = e.value;
      else {
        const n = parseFloat(e.value);
        out[f.k] = isNaN(n) ? (G['默认'] || {})[f.k] : n;
      }
    }));
    return out;
  }

  /** 套用预设 */
  function mmApplyPreset(pfx, key) {
    const p = (mmCfg()['预设'] || []).find(x => x.key === key);
    if (!p) return;
    const cur = Object.assign({}, mmCfg()['默认'] || {}, p.mm || {});
    (mmCfg()['分组'] || []).forEach(g => (g.fields || []).forEach(f => {
      const e = $id(pfx + 'mm_' + f.k);
      if (!e) return;
      const v = cur[f.k];
      if (f.type === 'check') e.checked = (v !== false && v !== 'false');
      else e.value = (v === undefined || v === null) ? '' : v;
    }));
    mmUpdateSummary(pfx);
    if (pfx === 'st' && p.params && Q.templates) {
      const k = $id('stKind') && $id('stKind').value;
      const t = Q.templates.find(x => x.kind === k);
      if (t && p.params) {
        Object.keys(p.params).forEach(kk => {
          const e = $id('sp_' + kk);
          if (e) e.value = p.params[kk];
        });
      }
    }
    const G = mmCfg();
    const pr = (G['预设'] || []).find(x => x.key === key);
    toast('已套用「' + (pr ? pr.name : key) + '」', 'ok');
  }

  /** 本地摘要（与后端 describe 同义，便于即时反馈） */
  function mmSummaryText(mm) {
    const m = mm || {};
    const noStop = String(m['止损方式'] || '') === '不设止损';
    const lotTxt = ({
      '固定手数': () => '固定 ' + (m['固定手数'] || 1) + ' 手',
      '保证金比例': () => '保证金占权益 ' + (m['资金比例%'] || 0) + '%',
      '风险固定': () => '单笔风险 ' + (m['单笔风险%'] || 0) + '%',
      '波动率目标': () => '波动率目标 ' + (m['目标波动%'] || 0) + '%',
      '分数凯利': () => '分数凯利 ×' + (m['凯利分数'] || 0.5),
    }[m['手数模式']] || (() => String(m['手数模式'] || '')))();
    const stopTxt = ({
      'ATR（波动止损）': () => 'ATR(' + (m['ATR周期'] || 14) + ')×' + (m['ATR倍数'] || 2) + ' 止损',
      '百分比': () => (m['止损%'] || 2) + '% 百分比止损',
      '固定跳数': () => (m['止损跳数'] || 0) + ' 跳止损',
      '不设止损': () => '不设止损',
    }[m['止损方式']] || (() => ''))();
    const p = ['仓位：' + lotTxt, '退出：' + stopTxt,
      '仓位上限：保证金 ≤' + (m['保证金占用上限%'] || 0) + '%权益'];
    if (Number(m['最大回撤熔断%']) > 0) p.push('熔断：回撤 ' + m['最大回撤熔断%'] + '%');
    if (Number(m['连亏减仓笔数']) > 0) p.push('连亏 ' + m['连亏减仓笔数'] + ' 笔手数 ×' + m['连亏折扣']);
    if (Number(m['连亏停手笔数']) > 0) p.push('连亏 ' + m['连亏停手笔数'] + ' 笔停手');
    if (Number(m['日内亏损上限%']) > 0) p.push('日内亏损 ' + m['日内亏损上限%'] + '% 停手');
    if (m['加仓模式'] === '金字塔加仓') p.push('金字塔加仓 ' + (m['最大加仓次数'] || 0) + ' 次');
    if (m['移动止损'] && m['移动止损'] !== '关闭') p.push('移动止损：' + m['移动止损']);
    if (noStop && m['手数模式'] === '风险固定') p.push('⚠ 不设止损时「风险固定」会自动退回固定手数');
    return p.join('；');
  }

  function mmUpdateSummary(pfx) {
    const e = $id(pfx + 'MmSum');
    if (e) e.textContent = mmSummaryText(mmCollect(pfx));
  }

  function mmBind(pfx, host) {
    const h = host || document;
    h.querySelectorAll('[data-mmpreset]').forEach(b => {
      b.addEventListener('click', () => mmApplyPreset(pfx, b.dataset.mmpreset));
    });
    (mmCfg()['分组'] || []).forEach(g => (g.fields || []).forEach(f => {
      const e = $id(pfx + 'mm_' + f.k);
      if (e) { e.addEventListener('input', () => mmUpdateSummary(pfx));
        e.addEventListener('change', () => mmUpdateSummary(pfx)); }
    }));
    mmUpdateSummary(pfx);
  }

  /* ==================================================================
     二、策略
     ================================================================== */
  async function loadStrategies() {
    const d = await api('/api/strategy/list');
    Q.strategies = d.strategies || [];
    renderStrategyList();
    renderStrategySelects();
    renderRunBox();
  }
  function renderStrategySelects() {
    const sel = $id('signalStrategy');
    if (sel) {
      sel.innerHTML = '<option value="">（选策略以叠加信号）</option>' +
        Q.strategies.map(s => '<option value="' + esc(s.id) + '">' + esc(s.name) + '</option>').join('');
      sel.value = Q.signalStrat || '';
    }
  }

  function paramText(kind) {
    const t = Q.templates.find(x => x.kind === kind);
    if (!t) return '';
    return Object.keys(t.params).map(k => k + '=' + t.params[k]).join('、');
  }

  function renderStrategyList() {
    const box = $id('stList');
    $id('stCount').textContent = Q.strategies.length + ' 个策略';
    if (!Q.strategies.length) { box.innerHTML = '<div class="mini">还没有策略，点右上角新建</div>'; return; }
    box.innerHTML = Q.strategies.map(s => {
      const sc = s.scope || {};
      const scopeTxt = sc.mode === 'all' ? '全部品种（主力连续）'
        : sc.mode === 'sector' ? '板块：' + (sc.value || []).join('/')
          : '指定合约：' + (sc.value || []).join('/');
      return '<div class="stcard' + (s.id === Q.curStrat ? ' on' : '') + '" data-id="' + esc(s.id) + '">' +
        '<div class="t"><b>' + esc(s.name) + '</b>' +
        (s.enabled ? '<span class="badge on">' + (s.dryRun ? '挂载·仅信号' : '挂载·自动下单') + '</span>'
          : '<span class="badge">未挂载</span>') + '</div>' +
        '<div class="m">' + esc(s['模板名'] || s.kind) + ' · ' + (PERIOD_LABEL[s.period] || s.period) +
        '<br>参数：' + esc(paramText(s.kind)) +
        '<br>品种池：' + esc(scopeTxt) +
        '<br><span class="mm-line">资金管理：' + esc(s['资金管理'] || mmSummaryText(s.mm)) + '</span>' +
        ((s.takeProfitPct) ? '<br>止盈：' + s.takeProfitPct + '%' : '') +
        (s['当前手数'] ? '<br>最近下单手数：' + s['当前手数'] + ' 手' : '') +
        (s['最后信号'] ? '<br><span style="color:#b06a05">最近信号：' + esc(s['最后信号']) + '</span>' : '') +
        '</div><div class="ops">' +
        '<button class="btn" data-op="edit">编辑</button>' +
        '<button class="btn" data-op="copy">复制</button>' +
        '<button class="btn" data-op="scan">选股</button>' +
        '<button class="btn ' + (s.enabled ? 'danger' : 'primary') + '" data-op="toggle">' +
        (s.enabled ? '停止' : '挂载') + '</button>' +
        (s.enabled ? '<button class="btn" data-op="dry">' + (s.dryRun ? '改为自动下单' : '改为仅记录') + '</button>' : '') +
        '<button class="btn danger" data-op="del">删除</button>' +
        '</div></div>';
    }).join('');
  }

  function renderRunBox() {
    const box = $id('stRunBox');
    const run = Q.strategies.filter(s => s.enabled);
    if (!run.length) { box.innerHTML = '<div class="mini">暂无挂载中的策略。挂载后后台每约 30 秒巡检一次。</div>'; return; }
    box.innerHTML = run.map(s =>
      '<div style="margin-bottom:12px">' +
      '<div class="t" style="margin-bottom:5px"><b>' + esc(s.name) + '</b> ' +
      '<span class="badge on">' + (s.dryRun ? '仅记录信号' : '自动下单') + '</span> ' +
      '<span class="mini">' + esc(s['最后检查'] || '等待首次巡检') + '　自动下单 ' +
      (s['自动下单数'] || 0) + ' 笔</span></div>' +
      '<div class="stlog">' + ((s['运行日志'] || []).length
        ? s['运行日志'].slice().reverse().map(l => '<div>[' + esc(l.ts) + '] ' + esc(l.text) + '</div>').join('')
        : '<div>尚无日志</div>') + '</div></div>').join('');
  }

  function kindOf(id) { const s = Q.strategies.find(x => x.id === id); return s ? s.kind : ''; }

  function buildStrategyForm(src) {
    const s = src || {};
    const kind = s.kind || 'ma_cross';
    const t = Q.templates.find(x => x.kind === kind) || Q.templates[0] || { kind: 'ma_cross', params: {} };
    const params = Object.assign({}, t.params, s.params || {});
    const sc = s.scope || { mode: 'all', value: [] };
    const dirs = Q.directions;
    const f = [];
    f.push('<div class="sec-h">① 选择策略模板</div>');
    f.push('<div class="fi wide"><label>策略模板</label><select id="stKind">' +
      Q.templates.map(x => '<option value="' + x.kind + '"' + (x.kind === kind ? ' selected' : '') + '>' +
        esc(x.name) + (x.star ? '（示例）' : '') + '</option>').join('') + '</select>' +
      '<div class="hint" id="stKindDesc">' + esc(t.desc) + '</div></div>');

    f.push('<div class="fi"><label>策略名称</label><input id="stName" value="' +
      esc(s.name || t.name) + '" maxlength="40"></div>');
    f.push('<div class="fi"><label>信号周期</label><select id="stPeriod">' +
      Object.keys(PERIOD_LABEL).map(k => '<option value="' + k + '"' +
        ((s.period || t.period || 'day') === k ? ' selected' : '') + '>' + PERIOD_LABEL[k] + '</option>').join('') +
      '</select></div>');

    f.push('<div class="sec-h">② 策略参数</div>');
    const paramKeys = Object.keys(t.params);
    paramKeys.forEach(k => {
      const v = params[k];
      if (k === '方向') {
        f.push('<div class="fi"><label>' + k + '</label><select id="sp_' + k + '">' +
          dirs.map(d => '<option value="' + d + '"' + (v === d ? ' selected' : '') + '>' + d + '</option>').join('') +
          '</select></div>');
      } else if (k === '算子') {
        f.push('<div class="fi"><label>' + k + '</label><select id="sp_' + k + '">' +
          ['上穿', '下穿', '大于', '小于'].map(d => '<option value="' + d + '"' + (v === d ? ' selected' : '') + '>' + d + '</option>').join('') +
          '</select></div>');
      } else if (k === '左值' || k === '右值') {
        f.push('<div class="fi"><label>' + k + '（指标表达式）</label><input id="sp_' + k + '" value="' +
          esc(v) + '" placeholder="如 MA(5) / MA(20) / BOLL(20,2) / CLOSE / 30"></div>');
      } else {
        f.push('<div class="fi"><label>' + k + '</label><input id="sp_' + k + '" value="' + esc(v) + '" type="number" step="any"></div>');
      }
    });

    f.push('<div class="sec-h">③ 交易品种池</div>');
    f.push('<div class="fi"><label>品种池</label><select id="stScopeMode">' +
      [['all', '全部品种（主力连续）'], ['sector', '按板块'], ['codes', '指定代码']]
        .map(([k, lab]) => '<option value="' + k + '"' + (sc.mode === k ? ' selected' : '') + '>' + lab + '</option>').join('') +
      '</select></div>');
    f.push('<div class="fi wide"><label>品种池内容（按板块模式选板块；按代码模式填代码，逗号分隔）</label>' +
      '<input id="stScopeValue" value="' + esc((sc.value || []).join(',')) + '" placeholder="如 黑色建材 或 RB0,M0,IF2610">' +
      '<div class="hint">可用板块：' + (Q.sectors || []).join('、') + '；代码可写主力连续（RB0）或具体合约（RB2610）</div></div>');

    f.push(mmBlockHTML('st', s.mm));
    f.push('<div class="sec-h">⑤ 其它</div>');
    f.push('<div class="fi"><label>止盈（%，0=不启用）</label><input id="stTP" type="number" step="any" min="0" value="' + (s.takeProfitPct || 0) + '"></div>');
    f.push('<div class="fi wide"><label>备注</label><input id="stNotice" value="' + esc(s.notice || '') + '" maxlength="200"></div>');

    f.push('<div class="acts">' +
      '<button class="btn primary" id="stSave">保存策略</button>' +
      '<button class="btn" id="stScanNow">保存并选股</button>' +
      '<button class="btn" id="stReset">清空表单</button>' +
      '<span class="mini">保存后可在左侧策略库中挂载为「策略交易」</span></div>');
    $id('stForm').innerHTML = f.join('');
    $id('stEditTitle').textContent = (s.id ? '编辑策略 · ' + s.name : '新建策略');

    $id('stKind').addEventListener('change', e => {
      const t2 = Q.templates.find(x => x.kind === e.target.value);
      $id('stKindDesc').textContent = t2 ? t2.desc : '';
      const keep = collectStrategy();
      keep.kind = e.target.value; keep.params = {};
      buildStrategyForm(Object.assign({}, s, keep, { params: {} }));
    });
    $id('stReset').addEventListener('click', () => { Q.editing = null; buildStrategyForm(null); });
    $id('stSave').addEventListener('click', () => saveStrategy(false));
    $id('stScanNow').addEventListener('click', () => saveStrategy(true));
    mmBind('st', $id('stForm'));
  }

  function collectStrategy() {
    const kind = $id('stKind').value;
    const t = Q.templates.find(x => x.kind === kind) || { params: {} };
    const params = {};
    Object.keys(t.params).forEach(k => {
      const inp = $id('sp_' + k);
      if (!inp) { params[k] = t.params[k]; return; }
      const raw = inp.value;
      if (k === '方向' || k === '算子' || k === '左值' || k === '右值') params[k] = raw.trim();
      else { const n = parseFloat(raw); params[k] = isNaN(n) ? t.params[k] : n; }
    });
    const mode = $id('stScopeMode').value;
    const vals = String($id('stScopeValue').value || '').split(/[,，\s]+/)
      .map(x => x.trim()).filter(Boolean);
    const mm = mmCollect('st');
    return {
      id: Q.editing && Q.editing.id ? Q.editing.id : '',
      name: $id('stName').value.trim() || t.name,
      kind: kind,
      period: $id('stPeriod').value,
      params: params,
      scope: { mode: mode, value: vals },
      mm: mm,
      lots: Math.max(1, parseInt(mm['固定手数'], 10) || 1),
      takeProfitPct: parseFloat($id('stTP').value) || 0,
      notice: $id('stNotice').value.trim(),
      enabled: Q.editing ? !!Q.editing.enabled : false,
      dryRun: Q.editing ? (Q.editing.dryRun !== false) : true,
    };
  }

  async function saveStrategy(thenScan) {
    let body;
    try { body = collectStrategy(); } catch (e) { toast('参数填写有误：' + e.message, 'err'); return; }
    const r = await api('/api/strategy/save', body);
    if (!r.ok) { toast(r.msg, 'err'); return; }
    Q.editing = r.strategy; Q.curStrat = r.strategy.id;
    toast(r.msg, 'ok');
    await loadStrategies();
    buildStrategyForm(r.strategy);
    if (thenScan) doScan();
  }

  async function doScan() {
    if (!Q.curStrat) { toast('请先在左侧选择一个策略', 'warn'); return; }
    const tb = $id('scanBody');
    tb.innerHTML = '<tr><td colspan="11" class="empty">正在扫描品种池…</td></tr>';
    $id('scanInfo').textContent = '扫描中…';
    try {
      const r = await api('/api/strategy/scan', {
        id: Q.curStrat,
        lookback: parseInt($id('scanLookback').value, 10) || 3,
      });
      if (!r.ok) { tb.innerHTML = '<tr><td colspan="11" class="empty">' + esc(r.msg) + '</td></tr>'; return; }
      $id('scanInfo').textContent = '策略「' + r.strategy.name + '」　池 ' + r.poolSize +
        ' 个标的，命中 ' + r.hitCount + ' 个　' + r.ts;
      if (!r.rows.length) {
        tb.innerHTML = '<tr><td colspan="11" class="empty">最近 ' + r.lookback + ' 根内没有信号</td></tr>';
        return;
      }
      tb.innerHTML = r.rows.map(x =>
        '<tr data-code="' + esc(x.tradeable || x.code) + '">' +
        '<td class="l"><b>' + esc(x.code) + '</b></td>' +
        '<td class="l">' + esc(x.name) + '</td>' +
        '<td class="l"><span class="tag">' + esc(x.sector) + '</span></td>' +
        '<td class="' + (x.side === '多' ? 'up' : 'down') + '"><b>' + x.action + x.side + '</b></td>' +
        '<td class="num">' + px(x.price) + '</td>' +
        '<td class="num">' + (x.barsAgo === 0 ? '当根' : x.barsAgo + ' 根前') + '</td>' +
        '<td class="num">' + px(x.live != null ? x.live : x.last) + '</td>' +
        '<td class="num ' + cls(x.liveZdf != null ? x.liveZdf : x.zdf) + '">' + pct(x.liveZdf != null ? x.liveZdf : x.zdf) + '</td>' +
        '<td class="l" style="max-width:300px">' + esc(x.reason) + '<div class="mini">' + esc(x.date) + '</div></td>' +
        '<td class="l">' + (x.tradeable ? '<b>' + esc(x.tradeable) + '</b>' : '<span class="mini">无主力合约</span>') + '</td>' +
        '<td class="plain">' +
        (x.tradeable ? '<button class="btn" data-op="chart">看图</button> ' +
          '<button class="btn primary" data-op="trade">下单</button>' : '') +
        '</td></tr>').join('');
      if (r.errors && r.errors.length) $id('scanInfo').textContent += '　（' + r.errors.length + ' 个标的取数失败）';
    } catch (e) {
      tb.innerHTML = '<tr><td colspan="11" class="empty">选股失败：' + esc(e.message) + '</td></tr>';
    }
  }

  function bindStrategy() {
    $id('btnNewStrategy').addEventListener('click', () => {
      Q.editing = null; Q.curStrat = ''; buildStrategyForm(null);
    });
    $id('btnScan').addEventListener('click', doScan);
    $id('stList').addEventListener('click', async e => {
      const op = e.target.closest('button[data-op]');
      const card = e.target.closest('.stcard');
      if (!card) return;
      const id = card.dataset.id;
      const s = Q.strategies.find(x => x.id === id);
      if (!s) return;
      Q.curStrat = id;
      if (!op) { Q.editing = s; renderStrategyList(); buildStrategyForm(s); return; }
      const o = op.dataset.op;
      if (o === 'edit') { Q.editing = s; renderStrategyList(); buildStrategyForm(s); }
      else if (o === 'copy') {
        Q.editing = null;
        buildStrategyForm(Object.assign({}, s, { id: '', name: s.name + ' 副本', enabled: false }));
      } else if (o === 'scan') { renderStrategyList(); doScan(); }
      else if (o === 'toggle') {
        const r = await api('/api/strategy/toggle', { id: id, enabled: !s.enabled });
        toast(r.msg, 'ok'); await loadStrategies();
      } else if (o === 'dry') {
        const r = await api('/api/strategy/toggle', { id: id, dryRun: !s.dryRun });
        toast(r.msg, 'ok'); await loadStrategies();
      } else if (o === 'del') {
        if (!confirm('确定删除策略「' + s.name + '」？')) return;
        const r = await api('/api/strategy/delete', { id: id });
        toast(r.msg, 'ok'); Q.editing = null; Q.curStrat = ''; await loadStrategies(); buildStrategyForm(null);
      }
    });
    $id('scanBody').addEventListener('click', e => {
      const b = e.target.closest('button[data-op]'); if (!b) return;
      const tr = b.closest('tr'); const code = tr.dataset.code;
      if (!code) return;
      if (b.dataset.op === 'chart') {
        gotoPage('market');
        Q.mkMode = 'main'; Q.mkKw = '';
        $id('mkMode').querySelectorAll('button').forEach(x => x.classList.toggle('on', x.dataset.mode === 'main'));
        Q.mkSel = code; renderMarketList(); loadChart();
      } else {
        gotoPage('trade');
        if (typeof selectContract === 'function') selectContract(code);
        const inp = $id('qSearch'); if (inp) { inp.value = code; inp.dispatchEvent(new Event('input')); }
      }
    });
  }

  /* ==================================================================
     三、回测
     ================================================================== */
  function buildBacktestForm() {
    const f = [];
    f.push('<div class="sec-h">标的与周期</div>');
    f.push('<div class="fi"><label>合约代码</label><input id="btCode" value="RB0" placeholder="主力连续 RB0 / 具体合约 RB2610">' +
      '<div class="hint">主力连续（RB0/M0/CU0）历史最长，适合回测</div></div>');
    f.push('<div class="fi"><label>信号周期</label><select id="btPeriod">' +
      Object.keys(PERIOD_LABEL).map(k => '<option value="' + k + '"' + (k === 'day' ? ' selected' : '') + '>' + PERIOD_LABEL[k] + '</option>').join('') + '</select></div>');
    f.push('<div class="fi"><label>开始日期（留空=最早）</label><input id="btStart" placeholder="2023-01-01"></div>');
    f.push('<div class="fi"><label>结束日期（留空=最新）</label><input id="btEnd" placeholder="2026-09-30"></div>');

    f.push('<div class="sec-h">策略</div>');
    f.push('<div class="fi"><label>策略来源</label><select id="btSrc">' +
      Q.strategies.map(s => '<option value="' + esc(s.id) + '">策略库：' + esc(s.name) + '</option>').join('') +
      '<option value="__tpl__">直接用模板（下方填写）</option></select></div>');
    f.push('<div class="fi"><label>模板</label><select id="btKind">' +
      Q.templates.map(t => '<option value="' + t.kind + '"' + (t.star ? ' selected' : '') + '>' + esc(t.name) + '</option>').join('') + '</select></div>');
    const t0 = Q.templates.find(t => t.star) || Q.templates[0] || { params: {} };
    f.push('<div class="fi"><label>模板参数</label><input id="btParams" value="' +
      esc(Object.keys(t0.params).map(k => k + '=' + t0.params[k]).join(';')) + '">' +
      '<div class="hint">格式 参数=值;参数=值，如 周期=5;方向=双向</div></div>');

    f.push('<div class="sec-h">资金与撮合</div>');
    f.push('<div class="fi"><label>初始资金（元）</label><input id="btCash" type="number" value="100000"></div>');
    f.push('<div class="fi"><label>滑点（跳）</label><input id="btSlip" type="number" value="1" step="any" min="0"></div>');
    f.push('<div class="fi"><label>止盈（%，0=关闭）</label><input id="btTP" type="number" value="0" step="any" min="0"></div>');
    f.push('<div class="fi"><label>手续费折扣</label><input id="btFee" type="number" value="1" step="any" min="0">' +
      '<div class="hint">1 = 东方财富公司标准；0.33 ≈ 交易所底线</div></div>');
    f.push('<div class="fi"><label>保证金倍数</label><input id="btMargin" type="number" value="1" step="any" min="0.01"></div>');
    f.push('<div class="fi"><label>主图指标</label><input id="btMain" value="MA(5,20)"></div>');
    f.push('<div class="fi"><label>副图指标</label><input id="btSub" value="VOL(5,10,20),MACD(12,26,9)"></div>');

    f.push(mmBlockHTML('bt', Q.btMm));
    f.push('<div class="acts"><button class="btn primary" id="btRun">开始回测</button>' +
      '<button class="btn" id="btRun5">批量：均线周期 5/10/20/30/60</button>' +
      '<span class="mini" id="btMsg"></span></div>');
    $id('btForm').innerHTML = f.join('');

    $id('btSrc').addEventListener('change', e => {
      const s = Q.strategies.find(x => x.id === e.target.value);
      if (s) {
        $id('btKind').value = s.kind;
        $id('btParams').value = Object.keys(s.params).map(k => k + '=' + s.params[k]).join(';');
        $id('btPeriod').value = s.period || 'day';
        if (s.mm) { Q.btMm = s.mm; mmSetValues('bt', s.mm); }
      }
    });
    $id('btKind').addEventListener('change', e => {
      const t = Q.templates.find(x => x.kind === e.target.value);
      if (t) $id('btParams').value = Object.keys(t.params).map(k => k + '=' + t.params[k]).join(';');
    });
    $id('btRun').addEventListener('click', () => runBacktest());
    $id('btRun5').addEventListener('click', () => runBacktest(null, [5, 10, 20, 30, 60]));
    mmBind('bt', $id('btForm'));
  }

  /** 把一份 mm 值写回表单（用于切换策略来源时同步） */
  function mmSetValues(pfx, mm) {
    const G = mmCfg();
    const cur = Object.assign({}, G['默认'] || {}, mm || {});
    (G['分组'] || []).forEach(g => (g.fields || []).forEach(f => {
      const e = $id(pfx + 'mm_' + f.k);
      if (!e) return;
      const v = cur[f.k];
      if (f.type === 'check') e.checked = (v !== false && v !== 'false');
      else e.value = (v === undefined || v === null) ? '' : v;
    }));
    mmUpdateSummary(pfx);
  }

  function parseParamText(txt, kind) {
    const t = Q.templates.find(x => x.kind === kind) || { params: {} };
    const out = Object.assign({}, t.params);
    String(txt || '').split(/[;；]+/).forEach(pair => {
      const i = pair.indexOf('=');
      if (i < 0) return;
      const k = pair.slice(0, i).trim(), v = pair.slice(i + 1).trim();
      if (!k) return;
      if (k === '方向' || k === '算子' || k === '左值' || k === '右值') out[k] = v;
      else { const n = parseFloat(v); out[k] = isNaN(n) ? v : n; }
    });
    return out;
  }

  async function runBacktest(override, sweep) {
    const srcSel = $id('btSrc').value;
    const isTpl = srcSel === '__tpl__';
    const kind = $id('btKind').value;
    const body = {
      code: $id('btCode').value.trim().toUpperCase() || 'RB0',
      period: $id('btPeriod').value,
      start: $id('btStart').value.trim(),
      end: $id('btEnd').value.trim(),
      cash: parseFloat($id('btCash').value) || 100000,
      slippageTicks: parseFloat($id('btSlip').value) || 0,
      takeProfitPct: parseFloat($id('btTP').value) || 0,
      feeDiscount: parseFloat($id('btFee').value) || 1,
      marginMult: parseFloat($id('btMargin').value) || 1,
      mm: mmCollect('bt'),
      main: $id('btMain').value.trim(),
      sub: $id('btSub').value.trim(),
      kind: kind,
      params: parseParamText($id('btParams').value, kind),
    };
    if (!isTpl) { body.strategyId = srcSel; body.kind = undefined; }
    if (override) Object.assign(body, override);

    $id('btMsg').textContent = '回测中…';
    $id('btRun').disabled = true;
    try {
      if (sweep && sweep.length) {
        const rows = [];
        for (const n of sweep) {
          const b = Object.assign({}, body, { params: Object.assign({}, body.params, { 周期: n }) });
          const r = await api('/api/backtest/run', b);
          if (r.ok) rows.push({ n: n, st: r.stats, id: r.id });
        }
        if (rows.length) {
          rows.sort((a, b) => b.st.总收益率 - a.st.总收益率);
          renderSweep(rows, body);
          const best = rows[0];
          const bb = Object.assign({}, body, { params: Object.assign({}, body.params, { 周期: best.n }) });
          const r = await api('/api/backtest/run', bb);
          if (r.ok) renderBacktest(r);
          $id('btMsg').textContent = '完成 ' + rows.length + ' 组';
        } else {
          $id('btMsg').textContent = '参数扫描全部失败';
        }
      } else {
        const r = await api('/api/backtest/run', body, 300000);
        if (!r.ok) { $id('btMsg').textContent = r.msg || '回测失败'; return; }
        renderBacktest(r);
        $id('btMsg').textContent = '完成，用时 ' + (r.item.cost || 0) + ' 秒';
      }
    } catch (e) { $id('btMsg').textContent = '回测失败：' + e.message; }
    finally { $id('btRun').disabled = false; }
  }

  function renderSweep(rows, body) {
    const box = document.createElement('div');
    box.className = 'card';
    box.id = 'btSweep';
    box.innerHTML = '<div class="hd2"><h3>参数扫描：均线周期</h3>' +
      '<span class="desc">' + esc(body.code) + ' · 同一套资金与手续费口径</span></div>' +
      '<div class="tw"><table><thead><tr><th>周期</th><th>总收益率</th><th>年化</th>' +
      '<th>最大回撤</th><th>交易次数</th><th>胜率</th><th>盈亏比</th><th>夏普</th></tr></thead>' +
      '<tbody>' + rows.map(r =>
        '<tr><td><b>MA' + r.n + '</b></td>' +
        '<td class="num ' + cls(r.st.总收益率) + '">' + pct(r.st.总收益率) + '</td>' +
        '<td class="num ' + cls(r.st.年化收益率) + '">' + pct(r.st.年化收益率) + '</td>' +
        '<td class="num">' + pct(-r.st.最大回撤) + '</td>' +
        '<td class="num">' + r.st.交易次数 + '</td>' +
        '<td class="num">' + fmt(r.st.胜率, 2) + '%</td>' +
        '<td class="num">' + (r.st.盈亏比 == null ? '--' : fmt(r.st.盈亏比, 2)) + '</td>' +
        '<td class="num">' + fmt(r.st.夏普比率, 2) + '</td></tr>').join('') +
      '</tbody></table></div>';
    const old = $id('btSweep');
    if (old) old.remove();
    $id('btResult').before(box);
  }

  function kpi(k, v, x, cls2) {
    return '<div class="kpibox' + (cls2 ? ' ' + cls2 : '') + '"><div class="k">' + k +
      '</div><div class="v ' + (cls2 === 'score' ? '' : '') + '">' + v + '</div>' +
      (x ? '<div class="x">' + x + '</div>' : '') + '</div>';
  }

  function renderBacktest(r) {
    const st = r.stats;
    const html = [];
    html.push('<div class="card"><div class="hd2"><h3>回测结果 · ' + esc(r.name) + ' ' + esc(r.code) + '</h3>' +
      '<span class="desc">' + esc(r.item.kind) + ' · ' + (PERIOD_LABEL[r.item.period] || r.item.period) +
      ' · ' + esc((r.bars[0] || {}).d || '') + ' ~ ' + esc((r.bars[r.bars.length - 1] || {}).d || '') +
      '（' + r.bars.length + ' 根，预热 ' + r.warmup + ' 根）</span></div>' +
      '<div class="bd"><div class="kpigrid">' +
      kpi('总收益率', pct(st.总收益率), '期末权益 ' + money(st.期末权益), 'score') +
      kpi('年化收益率', pct(st.年化收益率), '按' + (PERIOD_LABEL[r.item.period] || '') + '折算') +
      kpi('最大回撤', fmt(st.最大回撤, 2) + '%', st.最大回撤起点 + ' → ' + st.最大回撤终点) +
      kpi('交易次数', st.交易次数, '多 ' + st.多头交易 + ' / 空 ' + st.空头交易) +
      kpi('胜率', fmt(st.胜率, 2) + '%', '盈 ' + st.盈利次数 + ' / 亏 ' + st.亏损次数) +
      kpi('盈亏比', st.盈亏比 == null ? '--' : fmt(st.盈亏比, 2), '总盈利 / 总亏损') +
      kpi('夏普比率', fmt(st.夏普比率, 2), '按每根权益收益年化') +
      kpi('总盈亏', money(st.总盈亏), '平均每笔 ' + money(st.平均每笔盈亏)) +
      kpi('总手续费', money(st.总手续费), '占初始资金 ' + fmt(st.总手续费 / st.初始资金 * 100, 2) + '%') +
      kpi('最大连续亏损', st.最大连续亏损 + ' 笔', '平均持仓 ' + st.平均持仓根数 + ' 根') +
      kpi('最大单笔盈利', money(st.最大单笔盈利), '') +
      kpi('最大单笔亏损', money(st.最大单笔亏损), '') +
      '</div>' +
      '<div class="sec-h" style="margin-top:14px">资金管理与仓位</div>' +
      '<div class="kpigrid">' +
      kpi('手数模式', st.手数模式 || '--', '平均 ' + fmt(st.平均手数, 2) + ' 手 / 最大 ' + st.最大手数 + ' 手') +
      kpi('止损方式', st.止损方式 || '--', '止损离场 ' + (st.止损离场次数 || 0) + ' 次') +
      kpi('期望R', fmt(st.期望R, 3), '平均盈利 ' + fmt(st.平均盈利R, 2) + 'R / 亏损 ' + fmt(st.平均亏损R, 2) + 'R') +
      kpi('累计R', fmt(st.累计R, 2) + 'R', 'R = 净盈亏 ÷ 单笔风险额', 'score') +
      kpi('平均保证金占用', fmt(st['平均保证金占用%'], 1) + '%', '峰值 ' + fmt(st['最大保证金占用%'], 1) + '%') +
      kpi('熔断触发', (st.熔断次数 || 0) + ' 次', (st.熔断次数 ? '已按设定清仓/停手' : '未触发')) +
      kpi('风控拒单', st.风控拒单 || 0, '熔断 / 连亏 / 仓位上限拦截') +
      kpi('资金不足跳过', st.资金不足跳过 || 0, '可用资金不够的开仓') +
      '</div>' +
      '<div class="mm-sum" style="margin-top:10px">' + esc(st.资金管理方案 || '') + '</div>' +
      '<div class="mini" style="margin-top:10px">' +
      '撮合口径：第 i 根收盘出信号 → 第 i+1 根开盘成交；手数按「上一根收盘权益」由资金管理算出；滑点 ' + (r.meta.slippageTicks || 0) +
      ' 跳；止盈 ' + (r.meta.takeProfitPct || 0) + '%' +
      '；手续费折扣 ' + r.settings.手续费折扣 + '　保证金倍数 ' + r.settings.保证金倍数 +
      '（' + esc(r.variety) + ' 合约乘数 ' + fmt(r.spec.合约乘数, 0) +
      '，保证金率 ' + fmt(r.spec.保证金率 * 100, 1) + '%）' +
      '</div></div></div>');

    html.push('<div class="card"><div class="hd2"><h3>K 线与买卖点</h3>' +
      '<span class="desc">▲B 开多　▼S 开空　●平仓；曲线为所设主图指标</span></div><div class="bd">' +
      '<div class="chart3" id="btK" style="height:420px"></div></div></div>');

    html.push('<div class="card"><div class="hd2"><h3>净值曲线与回撤</h3>' +
      '<span class="desc">蓝=动态权益　红=回撤（右侧轴）</span></div><div class="bd">' +
      '<div class="chart3" id="btE"></div></div></div>');

    html.push('<div class="card"><div class="hd2"><h3>成交明细</h3>' +
      '<span class="desc">共 ' + r.trades.length + ' 笔' + (r.trades.length > 300 ? '（仅列出最近 300 笔）' : '') +
      '　R = 净盈亏 ÷ 单笔风险额</span></div>' +
      '<div class="tw"><table><thead><tr>' +
      '<th>#</th><th>方向</th><th>手数</th><th>开仓时间</th><th>开仓价</th>' +
      '<th>平仓时间</th><th>平仓价</th><th>持仓根数</th><th>止损价</th><th>风险额</th>' +
      '<th>毛盈亏</th><th>手续费</th>' +
      '<th>净盈亏</th><th>R</th><th>离场</th><th class="l">开仓理由</th><th class="l">平仓理由</th>' +
      '</tr></thead><tbody>' +
      r.trades.slice(-300).map((t, i) =>
        '<tr><td>' + (i + 1) + '</td>' +
        '<td class="' + (t.方向 === '多' ? 'up' : 'down') + '">' + t.方向 + '</td>' +
        '<td class="num">' + t.手数 + (t.加仓次数 ? '<span class="mini">+' + t.加仓次数 + '</span>' : '') + '</td>' +
        '<td class="l">' + esc(t.开仓时间) + '</td><td class="num">' + px(t.开仓价) + '</td>' +
        '<td class="l">' + esc(t.平仓时间) + '</td><td class="num">' + px(t.平仓价) + '</td>' +
        '<td class="num">' + t.持仓根数 + '</td>' +
        '<td class="num">' + px(t.止损价) + '</td>' +
        '<td class="num">' + fmt(t.风险额, 0) + '</td>' +
        '<td class="num ' + cls(t.毛盈亏) + '">' + fmt(t.毛盈亏, 2) + '</td>' +
        '<td class="num">' + fmt(t.手续费, 2) + '</td>' +
        '<td class="num ' + cls(t.净盈亏) + '"><b>' + fmt(t.净盈亏, 2) + '</b></td>' +
        '<td class="num ' + cls(t.盈亏R) + '">' + (t.盈亏R == null ? '--' : fmt(t.盈亏R, 2)) + '</td>' +
        '<td class="l"><span class="tag">' + esc(t.平仓类型 || '信号') + '</span></td>' +
        '<td class="l" style="max-width:220px">' + esc(t.开仓理由) + '</td>' +
        '<td class="l" style="max-width:250px">' + esc(t.平仓理由) + '</td></tr>').join('') +
      '</tbody></table></div></div>');

    $id('btResult').innerHTML = html.join('');
    Q.bt = r;
    Q.btK = echarts.init($id('btK'), null, { renderer: 'canvas' });
    Q.btE = echarts.init($id('btE'), null, { renderer: 'canvas' });
    drawBacktestK(r);
    drawBacktestEquity(r);
    setTimeout(resizeCharts, 60);
  }

  function drawBacktestK(r) {
    const bars = r.bars, cats = bars.map(b => dtLabel(b.d));
    const main = r.main || [], subs = r.sub || [];
    const ohlc = bars.map(b => [b.o, b.c, b.l, b.h]);
    const H = 420, subH = 90, gap = 20, topPad = 28, botPad = 50;
    const grids = [{ left: 62, right: 22, top: topPad, height: Math.max(140, H - topPad - botPad - subs.length * (subH + gap)) }];
    let y = grids[0].top + grids[0].height + gap;
    subs.forEach(() => { grids.push({ left: 62, right: 22, top: y, height: subH }); y += subH + gap; });
    const series = [{
      name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: ohlc,
      itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      markPoint: { data: markPoints(r.marks, bars, false, true).slice(-300) },
    }];
    main.forEach(it => it.lines.forEach(ln => series.push({
      name: ln.label, type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: ln.data,
      symbol: 'none', lineStyle: { width: 1.2, color: ln.color }, itemStyle: { color: ln.color },
    })));
    subs.forEach((it, si) => {
      const gi = si + 1;
      if (it.bars) series.push({
        name: it.bars.label, type: 'bar', xAxisIndex: gi, yAxisIndex: gi,
        data: it.bars.data.map((v, i) => v === null ? null : {
          value: v,
          itemStyle: { color: it.name === 'VOL' ? (bars[i].c >= bars[i].o ? hexA(UP, .85) : hexA(DOWN, .85)) : (v >= 0 ? UP : DOWN) },
        }), barWidth: '62%',
      });
      (it.lines || []).forEach(ln => series.push({
        name: ln.label, type: 'line', xAxisIndex: gi, yAxisIndex: gi, data: ln.data,
        symbol: 'none', lineStyle: { width: 1.1, color: ln.color }, itemStyle: { color: ln.color },
      }));
    });
    Q.btK.setOption({
      animation: false, backgroundColor: '#fff',
      legend: { top: 2, left: 60, type: 'scroll', itemWidth: 14, itemHeight: 8, textStyle: { fontSize: 11, color: '#5b6b80' } },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
        backgroundColor: 'rgba(255,255,255,.97)', borderColor: '#dfe6ef', textStyle: { color: '#1b2432', fontSize: 11.5 },
        formatter: ps => {
          if (!ps || !ps.length) return '';
          const i = ps[0].dataIndex, b = bars[i];
          let h = '<b>' + b.d + '</b><br>开 ' + px(b.o) + ' 高 ' + px(b.h) + '<br>低 ' + px(b.l) + ' 收 ' + px(b.c);
          (r.marks || []).filter(m => m.i === i).forEach(m => {
            h += '<br><span style="color:' + (m.side === '多' ? UP : DOWN) + '"><b>' + m.action + m.side +
              '</b> @ ' + px(m.price) + '</span><br><span style="color:#b06a05">' + esc(m.reason) + '</span>';
          });
          return h;
        },
      },
      grid: grids,
      xAxis: grids.map((g, i) => ({
        type: 'category', gridIndex: i, data: cats, axisTick: { show: false },
        axisLine: { lineStyle: { color: '#dfe6ef' } },
        axisLabel: { show: i === grids.length - 1, fontSize: 10.5, color: '#8b9aad' },
        axisPointer: { label: { show: false } },
      })),
      yAxis: grids.map((g, i) => ({
        gridIndex: i, scale: true, axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { fontSize: 10.5, color: '#8b9aad' }, splitLine: { lineStyle: { color: '#eef2f7' } },
      })),
      series: series,
      dataZoom: [
        { type: 'inside', xAxisIndex: grids.map((_, i) => i), start: 0, end: 100 },
        { type: 'slider', xAxisIndex: grids.map((_, i) => i), bottom: 6, height: 18, start: 0, end: 100, borderColor: '#dfe6ef', fillerColor: 'rgba(37,99,235,.10)', textStyle: { fontSize: 10, color: '#8b9aad' } },
      ],
    }, true);
  }

  function drawBacktestEquity(r) {
    const eq = r.equity, cats = eq.map(e => dtLabel(e[1]));
    const val = eq.map(e => e[2]);
    const dd = eq.map(e => -e[5]);
    const cash0 = r.stats.初始资金;
    Q.btE.setOption({
      animation: false, backgroundColor: '#fff',
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(255,255,255,.97)', borderColor: '#dfe6ef', textStyle: { color: '#1b2432', fontSize: 11.5 },
        formatter: ps => {
          if (!ps || !ps.length) return '';
          const i = ps[0].dataIndex, e = eq[i];
          const ret = (e[2] - cash0) / cash0 * 100;
          return '<b>' + e[1] + '</b><br>动态权益 ' + money(e[2]) +
            '<br><span style="color:' + chooseColor(ret) + '">累计 ' + pct(ret) + '</span>' +
            '<br>结存 ' + money(e[3]) + '<br>收盘 ' + px(e[4]) +
            '<br>保证金占用 ' + money(e[6]) + '<br>回撤 ' + fmt(-e[5], 2) + '%' +
            '<br>持仓 ' + (e[7] ? (e[7] > 0 ? '多' : '空') : '空仓');
        },
      },
      legend: { top: 2, left: 60, data: ['动态权益', '回撤'], textStyle: { fontSize: 11, color: '#5b6b80' }, itemWidth: 14, itemHeight: 8 },
      grid: { left: 78, right: 60, top: 30, bottom: 46 },
      xAxis: { type: 'category', data: cats, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe6ef' } }, axisLabel: { fontSize: 10.5, color: '#8b9aad' } },
      yAxis: [
        { type: 'value', scale: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10.5, color: '#8b9aad', formatter: v => (v / 10000).toFixed(1) + '万' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
        { type: 'value', scale: false, max: 0, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10.5, color: '#8b9aad', formatter: v => fmt(v, 0) + '%' }, splitLine: { show: false } },
      ],
      series: [
        {
          name: '动态权益', type: 'line', yAxisIndex: 0, data: val, symbol: 'none',
          lineStyle: { width: 1.6, color: '#2563eb' }, areaStyle: { color: 'rgba(37,99,235,.07)' },
          markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: '#c9d4e3', type: 'dashed' },
            label: { formatter: '初始 ' + (cash0 / 10000).toFixed(1) + '万', fontSize: 10, color: '#8b9aad' },
            data: [{ yAxis: cash0 }],
          },
        },
        { name: '回撤', type: 'line', yAxisIndex: 1, data: dd, symbol: 'none', lineStyle: { width: 1.1, color: '#d8342b' }, areaStyle: { color: 'rgba(216,52,43,.08)' } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0] }, { type: 'slider', xAxisIndex: [0], bottom: 6, height: 18, borderColor: '#dfe6ef', fillerColor: 'rgba(37,99,235,.10)', textStyle: { fontSize: 10, color: '#8b9aad' } }],
    }, true);
  }
  /* ==================================================================
     四、复盘
     ================================================================== */
  const RP_KEYS = ['序号', '时间', '动作', '方向', '手数', '价格', '成交额', '手续费',
    '净盈亏', '持仓根数', '权益', '收益率%', '回撤%', '可用资金', '保证金',
    '持仓方向', '持仓手数', '持仓均价', '止损价', '风险额', '盈亏R', '加仓',
    '来源', '说明'];
  const RP_NUM = { 手数: 1, 价格: 1, 成交额: 1, 手续费: 1, 净盈亏: 1, 权益: 1,
    可用资金: 1, 保证金: 1, 持仓手数: 1, 持仓均价: 1, 止损价: 1, 风险额: 1, 盈亏R: 1 };
  const RP_PCT = { '收益率%': 1, '回撤%': 1 };
  const RP_COLOR = { 净盈亏: 1, '收益率%': 1, 盈亏R: 1 };

  function rpVal(k, v) {
    if (v === null || v === undefined || v === '') return '--';
    if (RP_NUM[k]) return px(v);
    if (RP_PCT[k]) return pct(v);
    return String(v);
  }

  function buildReplayForm() {
    const f = [];
    f.push('<div class="sec-h wide">① 复盘区间</div>');
    f.push('<div class="fi"><label>合约代码</label><input id="rpCode" value="RB0"></div>');
    f.push('<div class="fi"><label>周期</label><select id="rpPeriod">' +
      Object.keys(PERIOD_LABEL).map(k => '<option value="' + k + '"' + (k === 'day' ? ' selected' : '') + '>' + PERIOD_LABEL[k] + '</option>').join('') + '</select></div>');
    f.push('<div class="fi"><label>起始日期（留空=最近 N 根）</label><input id="rpStart" placeholder="2026-01-01"></div>');
    f.push('<div class="fi"><label>复盘根数</label><input id="rpBars" type="number" value="120" min="30" max="2000"></div>');
    f.push('<div class="fi"><label>初始资金（元）</label><input id="rpCash" type="number" value="100000" min="1000"></div>');
    f.push('<div class="fi"><label>止盈（%，0=关闭）</label><input id="rpTP" type="number" value="0" step="any" min="0"></div>');

    f.push('<div class="sec-h wide">② 策略与交易方式</div>');
    f.push('<div class="fi"><label>叠加策略信号</label><select id="rpKind">' +
      '<option value="">不叠加</option>' +
      Q.templates.map(t => '<option value="' + t.kind + '"' + (t.star ? ' selected' : '') + '>' + esc(t.name) + '</option>').join('') +
      '</select><div class="hint">信号会画在 K 线上，作为「答案」参考</div></div>');
    f.push('<div class="fi"><label>交易方式</label><select id="rpAuto">' +
      '<option value="">手动复盘（自己点开平仓）</option>' +
      '<option value="1">自动跟随策略信号</option></select>' +
      '<div class="hint">自动模式下由策略出信号、资金管理算手数，得到可对照的资金曲线</div></div>');

    f.push('<div class="wide mm-host" id="rpMmHost"></div>');
    f.push('<div class="acts wide"><button class="btn primary" id="rpOpen">开始复盘</button>' +
      '<span class="mini" id="rpMsg">从区间第一根逐根揭示；可手动开平仓，也可自动跟随策略信号。</span></div>');
    $id('rpForm').innerHTML = f.join('');
    $id('rpMmHost').innerHTML = mmBlockHTML('rp', Q.rpMm);
    $id('rpOpen').addEventListener('click', openReplay);
    mmBind('rp', $id('rpForm'));
  }

  async function openReplay() {
    const kind = $id('rpKind').value;
    const tpl = Q.templates.find(t => t.kind === kind);
    const params = {};
    if (tpl) Object.keys(tpl.params).forEach(k => { params[k] = tpl.params[k]; });
    const body = {
      code: $id('rpCode').value.trim().toUpperCase() || 'RB0',
      period: $id('rpPeriod').value,
      start: $id('rpStart').value.trim(),
      bars: parseInt($id('rpBars').value, 10) || 120,
      cash: parseFloat($id('rpCash').value) || 100000,
      takeProfitPct: parseFloat($id('rpTP').value) || 0,
      kind: kind || '',
      params: params,
      mm: mmCollect('rp'),
      auto: !!$id('rpAuto').value,
    };
    Q.rpMm = body.mm;
    $id('rpMsg').textContent = '正在加载…';
    try {
      const r = await api('/api/replay/open', body, 180000);
      if (!r.ok) { $id('rpMsg').textContent = r.msg || '开启失败'; return; }
      Q.rp = r; Q.rpExpandAll = false; Q.rpView = 'group';
      $id('rpMsg').textContent = r.msg;
      renderReplay(r);
    } catch (e) { $id('rpMsg').textContent = '开启失败：' + e.message; }
  }

  function renderReplay(rp) {
    const auto = !!rp.auto;
    const h = [];
    h.push('<div class="card"><div class="hd2"><h3>' + esc(rp.name) + ' ' + esc(rp.code) +
      ' 复盘 · ' + (PERIOD_LABEL[rp.period] || rp.period) + '</h3>' +
      '<span class="desc">区间 ' + esc((rp.bars[0] || {}).d || '') + ' 起，共 ' +
      (rp.end - rp.start + 1) + ' 根；当前第 ' + (rp.idx - rp.start + 1) + ' 根（' + rp.progress + '%）</span></div>' +
      '<div class="bd">' +
      '<div class="rp-ctrl" id="rpCtrl">' +
      '<button class="btn" data-rp="home">⏮ 回到起点</button>' +
      '<button class="btn" data-rp="prev">◀ 上一根</button>' +
      '<button class="btn" data-rp="next">下一根 ▶</button>' +
      '<button class="btn" data-rp="next5">前进 5 根</button>' +
      '<button class="btn primary" id="rpPlay">▶ 自动播放</button>' +
      '<select id="rpSpeed" class="sel" style="min-width:96px"><option value="600">慢</option>' +
      '<option value="220" selected>中</option><option value="80">快</option></select>' +
      '<button class="btn" data-rp="end">跳到末尾 ⏭</button>' +
      '<span class="sp"></span>' +
      '<button class="btn ' + (auto ? '' : 'primary') + '" id="rpAutoBtn">' +
      (auto ? '✋ 关闭自动跟随' : '🤖 自动跟随信号') + '</button>' +
      '<span class="sep-v"></span>' +
      '<button class="btn buy" data-rp="open-long">开多</button>' +
      '<button class="btn sell" data-rp="open-short">开空</button>' +
      '<button class="btn" data-rp="flat">平仓</button>' +
      '<input id="rpLots" class="w60" type="number" value="1" min="1"> 手' +
      '<button class="btn" id="rpClear">清空我的操作</button>' +
      '<button class="btn danger" id="rpClose">结束复盘</button>' +
      '</div>' +
      '<input class="range" type="range" id="rpRange" min="0" max="' + (rp.end - rp.start) + '" value="' + (rp.idx - rp.start) + '">' +
      '<div class="mm-sum" style="margin-top:8px">' + esc(rp.mmText || '') + '</div>' +
      '</div></div>');

    h.push('<div class="card"><div class="hd2"><h3>K 线与成交点</h3>' +
      '<span class="desc">▲B 信号开多　▼S 信号开空　● 信号平仓　◆ 我的操作</span></div><div class="bd">' +
      '<div class="rp-side"><div><div class="kchart small" id="rpChart" style="height:500px"></div>' +
      '<div class="chart3" id="rpEqChart" style="height:280px;margin-top:10px"></div></div>' +
      '<div class="rp-info" id="rpInfo"></div></div></div></div>');

    h.push(recCardHTML(rp));
    $id('rpResult').innerHTML = h.join('');
    Q.rpChart = echarts.init($id('rpChart'), null, { renderer: 'canvas' });
    Q.rpEq = echarts.init($id('rpEqChart'), null, { renderer: 'canvas' });
    drawReplay(rp);
    bindReplayControls();
    bindRecTools();
  }

  function recCardHTML(rp) {
    const recs = rp.records || [];
    const recCount = (rp.stat && rp.stat['记录笔数']) || recs.length;
    const groups = groupRecords(recs);
    const closed = recs.filter(r => r.动作 === '平');
    return '<div class="card" id="rpRecCard"><div class="hd2"><h3>复盘记录</h3>' +
      '<span class="desc">' + recCount + ' 条动作 · ' + groups.length + ' 笔交易 · 已平仓 ' +
      closed.length + ' 笔</span>' +
      '<span class="sp-tools">' +
      '<button class="btn tiny" data-rec="expand">展开全部</button>' +
      '<button class="btn tiny" data-rec="collapse">收起全部</button>' +
      '<button class="btn tiny" data-rec="view">' + (Q.rpView === 'group' ? '切换为全部字段明细表' : '切换为折叠视图') + '</button>' +
      '</span></div>' +
      '<div class="bd"><div class="rec-tools">' +
      '<button class="btn" data-rec="copy-csv">复制 CSV</button>' +
      '<button class="btn" data-rec="copy-tsv">复制 TSV</button>' +
      '<button class="btn" data-rec="copy-txt">复制摘要</button>' +
      '<span class="sep-v"></span>' +
      '<button class="btn primary" data-rec="exp-csv">导出 CSV</button>' +
      '<button class="btn" data-rec="exp-tsv">导出 TSV</button>' +
      '<button class="btn" data-rec="exp-json">导出 JSON</button>' +
      '<button class="btn" data-rec="exp-jsonl">导出 JSONL</button>' +
      '<span class="mini">导出含元信息与资金管理方案，可直接用 Excel 打开；点表格行可展开该笔的全部字段</span>' +
      '</div>' +
      '<div class="tw" id="rpRecBox">' + recTableHTML(rp) + '</div>' +
      '</div></div>';
  }

  function groupRecords(recs) {
    const out = [];
    let cur = null;
    (recs || []).forEach(r => {
      if (r.动作 === '开') {
        cur = { open: r, close: null, n: out.length + 1 };
        out.push(cur);
      } else {
        if (cur && !cur.close) cur.close = r;
        else { cur = { open: null, close: r, n: out.length + 1 }; out.push(cur); }
      }
    });
    return out;
  }

  function recTableHTML(rp) {
    const recs = rp.records || [];
    if (!recs.length) {
      return '<table><tbody><tr><td class="empty">还没有记录。' +
        (rp.auto ? '自动跟随已打开，点「下一根 / 自动播放」即可产生记录。'
          : '用「开多 / 开空 / 平仓」记一笔，或打开「自动跟随信号」。') +
        '</td></tr></tbody></table>';
    }
    if (Q.rpView === 'flat') return recFlatHTML(recs);
    const groups = groupRecords(recs);
    const h = ['<table class="rec-table"><thead><tr>',
      '<th></th><th>#</th><th>方向</th><th>手数</th><th class="l">开仓</th>',
      '<th class="l">平仓</th><th>持仓</th><th>净盈亏</th><th>R</th>',
      '<th>权益</th><th>收益率</th><th class="l">来源</th></tr></thead><tbody>'];
    groups.forEach(g => {
      const o = g.open, c = g.close;
      const dir = (o || c).方向;
      const lots = (o || c).手数;
      const pnl = c ? c.净盈亏 : null;
      const r = c ? c.盈亏R : null;
      const eq = (c || o).权益;
      const ret = (c || o)['收益率%'];
      const src = ((o ? o.来源 : '') || '') +
        (c && (c.来源 || '') !== (o ? o.来源 : '') ? ' / ' + c.来源 : '');
      h.push('<tr class="grp" data-g="' + g.n + '">' +
        '<td class="tg">' + (Q.rpExpandAll ? '▼' : '▶') + '</td>' +
        '<td>' + g.n + '</td>' +
        '<td class="' + (dir === '多' ? 'up' : 'down') + '"><b>' + dir + '</b></td>' +
        '<td class="num">' + lots + '</td>' +
        '<td class="l">' + (o ? esc(String(o.时间).slice(0, 10)) + ' @' + px(o.价格) : '<span class="mini">—</span>') + '</td>' +
        '<td class="l">' + (c ? esc(String(c.时间).slice(0, 10)) + ' @' + px(c.价格) +
          '<span class="mini"> ×' + (c.持仓根数 || 0) + ' 根</span>' : '<span class="mini">持仓中</span>') + '</td>' +
        '<td class="num">' + (c ? (c.持仓根数 || 0) : '--') + '</td>' +
        '<td class="num ' + cls(pnl) + '"><b>' + (pnl == null ? '--' : fmt(pnl, 2)) + '</b></td>' +
        '<td class="num ' + cls(r) + '">' + (r == null ? '--' : fmt(r, 2) + 'R') + '</td>' +
        '<td class="num">' + money(eq) + '</td>' +
        '<td class="num ' + cls(ret) + '">' + pct(ret) + '</td>' +
        '<td class="l">' + esc(src) + '</td></tr>');
      h.push('<tr class="sub" data-g="' + g.n + '"' + (Q.rpExpandAll ? '' : ' hidden') +
        '><td colspan="12"><div class="rec-detail">' +
        [o, c].filter(Boolean).map(x => '<div class="leg">' + recKVHTML(x) + '</div>').join('') +
        '</div></td></tr>');
    });
    h.push('</tbody></table>');
    return h.join('');
  }

  function recKVHTML(r) {
    const keys = RP_KEYS.filter(k => r[k] !== undefined && r[k] !== null && r[k] !== '');
    return '<div class="leg-h"><span class="tag ' + (r.动作 === '开' ? 'open' : 'close') + '">' +
      r.动作 + r.方向 + '</span><span class="mini">' + esc(r.时间) + '　' + esc(r.来源 || '') + '</span></div>' +
      '<div class="kv">' + keys.map(k =>
        '<span class="kv-i"><i>' + esc(k) + '</i><b class="' +
        (RP_COLOR[k] ? cls(r[k]) : '') + '">' + esc(rpVal(k, r[k])) + '</b></span>').join('') + '</div>';
  }

  function recFlatHTML(recs) {
    const keys = RP_KEYS.filter(k => recs.some(r => r[k] !== undefined));
    return '<table class="rec-table"><thead><tr>' +
      keys.map(k => '<th class="' + (RP_NUM[k] || RP_PCT[k] || RP_COLOR[k] ? 'num' : 'l') + '">' + esc(k) + '</th>').join('') +
      '</tr></thead><tbody>' + recs.map(r =>
        '<tr>' + keys.map(k => {
          const c = RP_COLOR[k];
          return '<td class="' + (c ? cls(r[k]) : (RP_NUM[k] || RP_PCT[k] ? 'num' : 'l')) + '">' +
            esc(rpVal(k, r[k])) + '</td>';
        }).join('') + '</tr>').join('') + '</tbody></table>';
  }

  function bindRecTools() {
    const card = $id('rpRecCard');
    if (!card || card.dataset.bound) return;
    card.dataset.bound = '1';
    card.addEventListener('click', async e => {
      const g = e.target.closest('tr.grp');
      if (g && !e.target.closest('button')) {
        const sub = card.querySelector('tr.sub[data-g="' + g.dataset.g + '"]');
        if (sub) {
          sub.hidden = !sub.hidden;
          const tg = g.querySelector('.tg');
          if (tg) tg.textContent = sub.hidden ? '▶' : '▼';
        }
        return;
      }
      const b = e.target.closest('button[data-rec]');
      if (!b) return;
      const op = b.dataset.rec;
      if (op === 'expand' || op === 'collapse') {
        Q.rpExpandAll = (op === 'expand');
        card.querySelectorAll('tr.sub').forEach(s => { s.hidden = !Q.rpExpandAll; });
        card.querySelectorAll('tr.grp .tg').forEach(t => { t.textContent = Q.rpExpandAll ? '▼' : '▶'; });
      } else if (op === 'view') {
        Q.rpView = (Q.rpView === 'group' ? 'flat' : 'group');
        $id('rpRecBox').innerHTML = recTableHTML(Q.rp);
        b.textContent = (Q.rpView === 'group' ? '切换为全部字段明细表' : '切换为折叠视图');
      } else if (op === 'copy-csv' || op === 'copy-tsv' || op === 'copy-txt') {
        await rpCopy(op.slice(5));
      } else if (op.indexOf('exp-') === 0) {
        rpExport(op.slice(4));
      }
    });
  }

  /* ---------- 复制 / 导出（纯前端生成，复盘结束后同样可用） ---------- */
  function copyText(t) {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(t).catch(() => { fallbackCopy(t); });
    }
    fallbackCopy(t);
    return Promise.resolve();
  }
  function fallbackCopy(t) {
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.left = '-9999px';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) { /* ignore */ }
    ta.remove();
  }

  function recMeta(rp) {
    const st = rp.stat || {};
    const bars = rp.bars || [];
    return {
      '合约': rp.code, '名称': rp.name,
      '周期': PERIOD_LABEL[rp.period] || rp.period,
      '区间': (rp.start != null ? '' : '') + ((bars[0] || {}).d || '') + ' ~ ' + (rp.cur ? rp.cur.d : ''),
      '初始资金': rp.cash0,
      '交易方式': rp.auto ? '自动跟随策略信号' : '手动复盘',
      '资金管理': rp.mmText || '',
      '统计': st,
    };
  }

  function recKeysOf(recs) {
    return RP_KEYS.filter(k => (recs || []).some(r => r[k] !== undefined));
  }

  function recCell(k, v, sep) {
    let s = (v === null || v === undefined) ? '' : String(v);
    if (sep === ',') {
      if (/[",\n]/.test(s)) s = '"' + s.replace(/"/g, '""') + '"';
    } else if (sep === '\t') {
      s = s.replace(/[\t\n]/g, ' ');
    }
    return s;
  }

  function recExportText(rp, fmt) {
    const recs = rp.records || [];
    const _p2 = n => String(n).padStart(2, '0');
    const _d = new Date();
    const stamp = '' + _d.getFullYear() + _p2(_d.getMonth() + 1) + _p2(_d.getDate()) +
      _p2(_d.getHours()) + _p2(_d.getMinutes()) + _p2(_d.getSeconds());
    const base = '复盘_' + rp.code + '_' + rp.period + '_' + stamp;
    if (fmt === 'json') {
      return { filename: base + '.json', text: JSON.stringify(
        { meta: recMeta(rp), records: recs, equity: rp.equity || [] }, null, 1) };
    }
    if (fmt === 'jsonl') {
      const lines = [JSON.stringify(recMeta(rp))];
      recs.forEach(r => lines.push(JSON.stringify(r)));
      return { filename: base + '.jsonl', text: lines.join('\n') };
    }
    if (fmt === 'txt') return { filename: base + '.txt', text: recTextSummary(rp) };
    const sep = (fmt === 'tsv') ? '\t' : ',';
    const keys = recKeysOf(recs);
    const head = [];
    const meta = recMeta(rp);
    Object.keys(meta).forEach(k => {
      if (k === '统计') return;
      head.push('# ' + k + '=' + meta[k]);
    });
    Object.keys(meta['统计'] || {}).forEach(k => {
      if (k === '持仓') return;
      head.push('# 统计.' + k + '=' + meta['统计'][k]);
    });
    const lines = head.concat([keys.join(sep)]);
    recs.forEach(r => lines.push(keys.map(k => recCell(k, r[k], sep)).join(sep)));
    return { filename: base + (fmt === 'tsv' ? '.tsv' : '.csv'), text: lines.join('\n') };
  }

  function padW(s, n) {
    s = String(s == null ? '' : s);
    let w = 0;
    for (const ch of s) w += (ch.charCodeAt(0) > 255 ? 2 : 1);
    return s + ' '.repeat(Math.max(0, n - w));
  }

  function recTextSummary(rp) {
    const st = rp.stat || {};
    const groups = groupRecords(rp.records || []);
    const L = ['复盘记录 · ' + rp.name + ' ' + rp.code + ' · ' +
      (PERIOD_LABEL[rp.period] || rp.period),
      '当前日期 ' + (rp.cur ? rp.cur.d : '') + '　初始资金 ' + rp.cash0 +
      '　当前权益 ' + (st.当前权益 != null ? st.当前权益 : '-'),
      '资金管理：' + (rp.mmText || '-'),
      '已平仓 ' + (st.已平仓 || 0) + ' 笔　胜率 ' + (st.胜率 || 0) + '%　盈亏比 ' +
      (st.盈亏比 == null ? '-' : st.盈亏比) + '　净盈亏 ' + (st.净盈亏 || 0) +
      '　累计R ' + (st.累计R || 0) + '　手续费 ' + (st.总手续费 || 0),
      '收益率 ' + (st.收益率 || 0) + '%　最大回撤 ' + (st.最大回撤 || 0) + '%', ''];
    L.push(padW('#', 4) + padW('方向', 6) + padW('手数', 6) + padW('开仓', 24) +
      padW('平仓', 24) + padW('持仓', 6) + padW('净盈亏', 12) + padW('R', 9) +
      padW('权益', 13) + '来源');
    groups.forEach(g => {
      const o = g.open, c = g.close;
      L.push(padW(g.n, 4) + padW((o || c).方向, 6) + padW((o || c).手数, 6) +
        padW(o ? String(o.时间).slice(0, 10) + ' @' + o.价格 : '-', 24) +
        padW(c ? String(c.时间).slice(0, 10) + ' @' + c.价格 : '持仓中', 24) +
        padW(c ? (c.持仓根数 || 0) : '-', 6) +
        padW(c && c.净盈亏 != null ? c.净盈亏 : '-', 12) +
        padW(c && c.盈亏R != null ? c.盈亏R + 'R' : '-', 9) +
        padW((c || o).权益, 13) + ((o ? o.来源 : '') || ''));
    });
    return L.join('\n');
  }

  async function rpCopy(fmt) {
    if (!Q.rp) return;
    const t = recExportText(Q.rp, fmt === 'csv' ? 'csv' : (fmt === 'tsv' ? 'tsv' : 'txt'));
    await copyText(t.text);
    toast('已复制 ' + t.text.length + ' 字符到剪贴板', 'ok');
  }

  function downloadText(name, text, fmt) {
    const bom = (fmt === 'csv' || fmt === 'tsv') ? '\ufeff' : '';
    const blob = new Blob([bom + text], { type: 'text/plain;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 600);
  }

  function rpExport(fmt) {
    if (!Q.rp) return;
    const t = recExportText(Q.rp, fmt);
    downloadText(t.filename, t.text, fmt);
    toast('已导出 ' + t.filename + '（' + (Q.rp.records || []).length + ' 条记录）', 'ok');
  }

  function rpInfoHTML(rp) {
    const c = rp.cur || {}, st = rp.stat || {};
    const p = rp.pos;
    const rows = [
      ['当前日期', c.d || '--'],
      ['开 / 高', px(c.o) + ' / ' + px(c.h)],
      ['低 / 收', px(c.l) + ' / ' + px(c.c)],
      ['成交量', fmtInt(c.v)],
      ['持仓量', c.p ? fmtInt(c.p) : '--'],
    ];
    let h = '<h4>当前 K 线</h4>';
    h += rows.map(r => '<div class="row"><span>' + r[0] + '</span><span>' + r[1] + '</span></div>').join('');
    h += '<h4 style="margin-top:12px">复盘持仓</h4>';
    if (p) {
      h += '<div class="row"><span>方向 / 手数</span><span class="' + (p.dir > 0 ? 'up' : 'down') +
        '">' + (p.dir > 0 ? '多' : '空') + ' ' + p.lots + ' 手</span></div>' +
        '<div class="row"><span>持仓均价</span><span>' + px(p.avg) + '</span></div>' +
        (p.stop ? '<div class="row"><span>止损价</span><span>' + px(p.stop) + '</span></div>' : '') +
        (p.risk ? '<div class="row"><span>风险额</span><span>' + fmt(p.risk, 0) + '</span></div>' : '') +
        '<div class="row"><span>浮动盈亏</span><span class="' + cls(st.浮动盈亏) + '"><b>' +
        fmt(st.浮动盈亏, 2) + '</b></span></div>';
    } else { h += '<div class="mini">当前空仓</div>'; }

    h += '<h4 style="margin-top:12px">资金</h4>' +
      '<div class="row"><span>初始资金</span><span>' + money(st.初始资金) + '</span></div>' +
      '<div class="row"><span>当前权益</span><span><b>' + money(st.当前权益) + '</b></span></div>' +
      '<div class="row"><span>收益率</span><span class="' + cls(st.收益率) + '"><b>' + pct(st.收益率) + '</b></span></div>' +
      '<div class="row"><span>最大回撤</span><span class="down">' + fmt(st.最大回撤, 2) + '%</span></div>' +
      '<div class="row"><span>当前回撤</span><span>' + fmt(st.当前回撤, 2) + '%</span></div>' +
      '<div class="row"><span>总手续费</span><span>' + fmt(st.总手续费, 2) + '</span></div>';

    h += '<h4 style="margin-top:12px">复盘统计</h4>' +
      '<div class="row"><span>记录笔数</span><span>' + (st.记录笔数 || 0) + '</span></div>' +
      '<div class="row"><span>已平仓</span><span>' + (st.已平仓 || 0) + '</span></div>' +
      '<div class="row"><span>胜率</span><span>' + fmt(st.胜率, 2) + '%</span></div>' +
      '<div class="row"><span>盈亏比</span><span>' + (st.盈亏比 == null ? '--' : fmt(st.盈亏比, 2)) + '</span></div>' +
      '<div class="row"><span>累计R</span><span>' + fmt(st.累计R, 2) + 'R</span></div>' +
      '<div class="row"><span>净盈亏</span><span class="' + cls(st.净盈亏) + '"><b>' +
      fmt(st.净盈亏, 2) + '</b></span></div>' +
      '<div class="row"><span>平均持仓</span><span>' + fmt(st.平均持仓根数, 1) + ' 根</span></div>';

    if (rp.marks && rp.marks.length) {
      h += '<h4 style="margin-top:12px">最近策略信号</h4>' +
        rp.marks.slice(-4).reverse().map(m =>
          '<div class="row"><span>' + esc(String(m.d).slice(5)) + '</span><span class="' +
          (m.side === '多' ? 'up' : 'down') + '">' + m.action + m.side + ' @ ' + px(m.price) + '</span></div>').join('');
    }
    const recs = (rp.records || []);
    if (recs.length) {
      h += '<h4 style="margin-top:12px">最近记录</h4>' +
        recs.slice(-6).reverse().map(x =>
          '<div class="row"><span>' + esc(String(x.时间).slice(5, 10)) + ' ' + x.动作 + x.方向 + ' ' + x.手数 + '手</span>' +
          '<span class="' + (x.净盈亏 == null ? '' : cls(x.净盈亏)) + '">' + px(x.价格) +
          (x.净盈亏 == null ? '' : ' / ' + fmt(x.净盈亏, 2)) + '</span></div>').join('');
    }
    return h;
  }

  function drawReplay(rp) {
    const bars = rp.bars || [], cats = bars.map(b => dtLabel(b.d));
    const ohlc = bars.map(b => [b.o, b.c, b.l, b.h]);
    const marks = (rp.marks || []).map(m => {
      const long = m.side === '多', isOpen = m.action === '开';
      return {
        name: m.action + m.side, coord: [m.i, m.price], value: m.price,
        symbol: isOpen ? 'pin' : 'circle', symbolSize: isOpen ? 21 : 12,
        label: { formatter: isOpen ? (long ? 'B' : 'S') : '平', fontSize: 9, color: '#fff' },
        itemStyle: { color: isOpen ? (long ? UP : DOWN) : '#fff', borderColor: long ? UP : DOWN, borderWidth: 1.6 },
      };
    });
    (rp.records || []).forEach(x => {
      marks.push({
        name: x.动作 + x.方向, coord: [x.i, x.价格], value: x.价格,
        symbol: 'diamond', symbolSize: 15,
        label: { formatter: x.动作 === '开' ? '开' : '平', fontSize: 9, color: '#fff' },
        itemStyle: { color: '#7c3aed' },
      });
    });
    const vol = bars.map(b => ({
      value: b.v || 0,
      itemStyle: { color: b.c >= b.o ? hexA(UP, .8) : hexA(DOWN, .8) },
    }));
    Q.rpChart.setOption({
      animation: false, backgroundColor: '#fff',
      legend: { top: 2, left: 60, data: ['K线', '成交量'], textStyle: { fontSize: 11, color: '#5b6b80' }, itemWidth: 14, itemHeight: 8 },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
        backgroundColor: 'rgba(255,255,255,.97)', borderColor: '#dfe6ef', textStyle: { color: '#1b2432', fontSize: 11.5 },
        formatter: ps => {
          if (!ps || !ps.length) return '';
          const i = ps[0].dataIndex, b = bars[i];
          let h = '<b>' + b.d + '</b><br>开 ' + px(b.o) + ' 高 ' + px(b.h) + '<br>低 ' + px(b.l) + ' 收 ' + px(b.c) +
            '<br>量 ' + fmtInt(b.v);
          (rp.marks || []).filter(m => m.i === i).forEach(m => {
            h += '<br><span style="color:' + (m.side === '多' ? UP : DOWN) + '"><b>信号 ' + m.action + m.side +
              '</b> @ ' + px(m.price) + '</span><br><span style="color:#b06a05">' + esc(m.reason) + '</span>';
          });
          (rp.records || []).filter(x => x.i === i).forEach(x => {
            h += '<br><span style="color:#7c3aed"><b>我的 ' + x.动作 + x.方向 + ' ' + x.手数 + '手</b> @ ' + px(x.价格) +
              (x.净盈亏 == null ? '' : ' → 净盈亏 ' + fmt(x.净盈亏, 2)) + '</span>';
          });
          return h;
        },
      },
      grid: [{ left: 62, right: 22, top: 28, height: 340 }, { left: 62, right: 22, top: 388, height: 84 }],
      xAxis: [
        { type: 'category', gridIndex: 0, data: cats, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe6ef' } }, axisLabel: { show: false } },
        { type: 'category', gridIndex: 1, data: cats, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe6ef' } }, axisLabel: { fontSize: 10.5, color: '#8b9aad' } },
      ],
      yAxis: [
        { gridIndex: 0, scale: true, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10.5, color: '#8b9aad' }, splitLine: { lineStyle: { color: '#eef2f7' } } },
        { gridIndex: 1, scale: true, splitNumber: 2, axisLine: { show: false }, axisTick: { show: false }, axisLabel: { fontSize: 10.5, color: '#8b9aad', formatter: v => fmtInt(v) }, splitLine: { show: false } },
      ],
      series: [
        { name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: ohlc, itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN }, markPoint: { data: marks } },
        { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: vol, barWidth: '66%' },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0, 1] }],
    }, true);
    $id('rpInfo').innerHTML = rpInfoHTML(rp);
    drawReplayEquity(rp);
  }

  /** 资金变化曲线：动态权益 / 结存 / 浮动盈亏 / 回撤 + 我的成交点 */
  function drawReplayEquity(rp) {
    const eq = rp.equity || [];
    if (!Q.rpEq) return;
    if (!eq.length) { Q.rpEq.clear(); return; }
    const cats = eq.map(e => dtLabel(e[1]));
    const equity = eq.map(e => e[2]);
    const cashArr = eq.map(e => e[3]);
    const fpArr = eq.map(e => (e[10] == null ? 0 : e[10]));
    const ddArr = eq.map(e => -(e[5] || 0));
    const cash0 = rp.cash0 || equity[0];
    // 轴刻度精度随波动幅度自适应，避免窄幅区间下刻度全是同一个数
    const eqSpan = Math.max.apply(null, equity) - Math.min.apply(null, equity);
    const eqFmt = eqSpan < 300 ? (v => fmt(v, 0))
      : (v => (v / 10000).toFixed(eqSpan < 3000 ? 2 : 1) + '万');
    const rmarks = (rp.records || []).map(x => ({
      name: x.动作 + x.方向, coord: [x.i, x.权益],
      symbol: 'circle', symbolSize: 9,
      label: { show: false },
      itemStyle: { color: x.动作 === '开' ? '#7c3aed' : '#8b9aad' },
    }));
    Q.rpEq.setOption({
      animation: false, backgroundColor: '#fff',
      legend: { top: 2, left: 60, data: ['动态权益', '结存', '浮动盈亏', '回撤'],
        textStyle: { fontSize: 11, color: '#5b6b80' }, itemWidth: 14, itemHeight: 8 },
      tooltip: {
        trigger: 'axis', axisPointer: { type: 'cross' },
        backgroundColor: 'rgba(255,255,255,.97)', borderColor: '#dfe6ef',
        textStyle: { color: '#1b2432', fontSize: 11.5 },
        formatter: ps => {
          if (!ps || !ps.length) return '';
          const i = ps[0].dataIndex, e = eq[i];
          const ret = cash0 ? (e[2] - cash0) / cash0 * 100 : 0;
          return '<b>' + e[1] + '</b><br>动态权益 ' + money(e[2]) +
            '<br><span style="color:' + chooseColor(ret) + '">累计 ' + pct(ret) + '</span>' +
            '<br>结存 ' + money(e[3]) + '　浮动 ' + fmt(e[10] || 0, 2) +
            '<br>收盘 ' + px(e[4]) + '　保证金 ' + money(e[6]) +
            '<br>回撤 ' + fmt(e[5] || 0, 2) + '%' +
            '<br>持仓 ' + (e[7] ? (e[7] > 0 ? '多 ' : '空 ') + (e[8] || 0) + ' 手' : '空仓');
        },
      },
      grid: { left: 78, right: 60, top: 28, bottom: 44 },
      xAxis: { type: 'category', data: cats, axisTick: { show: false }, axisLine: { lineStyle: { color: '#dfe6ef' } }, axisLabel: { fontSize: 10.5, color: '#8b9aad' } },
      yAxis: [
        { type: 'value', scale: true, axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { fontSize: 10.5, color: '#8b9aad', formatter: eqFmt },
          splitLine: { lineStyle: { color: '#eef2f7' } } },
        { type: 'value', scale: false, max: 0, axisLine: { show: false }, axisTick: { show: false },
          axisLabel: { fontSize: 10.5, color: '#8b9aad', formatter: v => fmt(v, 0) + '%' }, splitLine: { show: false } },
        { type: 'value', scale: true, show: false, axisLine: { show: false }, axisTick: { show: false },
          splitLine: { show: false } },
      ],
      series: [
        { name: '动态权益', type: 'line', yAxisIndex: 0, data: equity, symbol: 'none',
          lineStyle: { width: 1.8, color: '#2563eb' }, areaStyle: { color: 'rgba(37,99,235,.08)' },
          markPoint: { data: rmarks, symbolSize: 9, label: { show: false } },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: '#c9d4e3', type: 'dashed' },
            label: { formatter: '初始 ' + fmt(cash0, 0), fontSize: 10, color: '#8b9aad' },
            data: [{ yAxis: cash0 }] } },
        { name: '结存', type: 'line', yAxisIndex: 0, data: cashArr, symbol: 'none',
          lineStyle: { width: 1.1, color: '#e8a33d', type: 'dashed' } },
        { name: '浮动盈亏', type: 'bar', yAxisIndex: 2, data: fpArr, barWidth: '55%',
          itemStyle: { color: p => (p.value >= 0 ? hexA(UP, .35) : hexA(DOWN, .35)) } },
        { name: '回撤', type: 'line', yAxisIndex: 1, data: ddArr, symbol: 'none',
          lineStyle: { width: 1.1, color: '#d8342b' }, areaStyle: { color: 'rgba(216,52,43,.08)' } },
      ],
      dataZoom: [{ type: 'inside', xAxisIndex: [0] },
        { type: 'slider', xAxisIndex: [0], bottom: 4, height: 16, borderColor: '#dfe6ef', fillerColor: 'rgba(37,99,235,.10)', textStyle: { fontSize: 10, color: '#8b9aad' } }],
    }, true);
  }

  function updateReplayUI(rp) {
    Q.rp = rp;
    const rg = $id('rpRange');
    if (rg) rg.value = rp.idx - rp.start;
    const desc = document.querySelector('#rpResult .hd2 .desc');
    if (desc) desc.textContent = '区间 ' + ((rp.bars[0] || {}).d || '') + ' 起，共 ' +
      (rp.end - rp.start + 1) + ' 根；当前第 ' + (rp.idx - rp.start + 1) + ' 根（' + rp.progress + '%）';
    drawReplay(rp);
    const box = $id('rpRecBox');
    if (box) box.innerHTML = recTableHTML(rp);
    const sub = document.querySelector('#rpRecCard .hd2 .desc');
    if (sub) {
      const groups = groupRecords(rp.records || []);
      const closed = (rp.records || []).filter(r => r.动作 === '平');
      sub.textContent = ((rp.stat && rp.stat['记录笔数']) || (rp.records || []).length) +
        ' 条动作 · ' + groups.length + ' 笔交易 · 已平仓 ' + closed.length + ' 笔';
    }
  }

  function rpAlive() {
    if (!Q.rp || !Q.rp.session) { toast('复盘已结束，请重新「开始复盘」', 'warn'); return false; }
    return true;
  }

  function bindReplayControls() {
    const box = $id('rpResult');
    box.addEventListener('click', async e => {
      const b = e.target.closest('button[data-rp]');
      if (b) {
        if (!rpAlive()) return;
        const op = b.dataset.rp;
        if (op === 'home') await rpSeek(Q.rp.start);
        else if (op === 'prev') await rpStep(-1);
        else if (op === 'next') await rpStep(1);
        else if (op === 'next5') await rpStep(5);
        else if (op === 'end') await rpStep(Q.rp.end - Q.rp.idx);
        else if (op === 'open-long') await rpTrade('多');
        else if (op === 'open-short') await rpTrade('空');
        else if (op === 'flat') await rpFlat();
        return;
      }
      if (e.target.id === 'rpPlay') { if (rpAlive()) togglePlay(); }
      if (e.target.id === 'rpClose') closeReplay();
      if (e.target.id === 'rpAutoBtn') await rpToggleAuto();
      if (e.target.id === 'rpClear') await rpClear();
    });
    const rg = $id('rpRange');
    if (rg) {
      rg.addEventListener('change', async () => {
        if (!rpAlive()) return;
        await rpSeek(Q.rp.start + parseInt(rg.value, 10));
      });
    }
    const sp = $id('rpSpeed');
    if (sp) sp.addEventListener('change', e => { Q.rpSpeed = parseInt(e.target.value, 10); });
  }

  function togglePlay() {
    const btn = $id('rpPlay');
    if (!btn) return;
    if (Q.rpTimer) {
      clearInterval(Q.rpTimer); Q.rpTimer = null;
      btn.textContent = '▶ 自动播放'; btn.classList.add('primary');
      return;
    }
    btn.textContent = '⏸ 暂停'; btn.classList.remove('primary');
    Q.rpTimer = setInterval(async () => {
      if (!Q.rp || Q.rp.idx >= Q.rp.end) { togglePlay(); return; }
      await rpStep(1);
    }, Q.rpSpeed);
  }

  async function rpStep(n) {
    if (!rpAlive()) return;
    const r = await api('/api/replay/step', { id: Q.rp.session, n: n });
    if (r.ok) updateReplayUI(r);
  }
  async function rpSeek(idx) {
    if (!rpAlive()) return;
    const r = await api('/api/replay/seek', { id: Q.rp.session, idx: idx });
    if (r.ok) { updateReplayUI(r); $id('rpRange').value = r.idx - r.start; }
  }
  async function rpTrade(side) {
    if (!rpAlive()) return;
    const lots = parseInt(($id('rpLots') || {}).value, 10) || 1;
    const r = await api('/api/replay/trade', { id: Q.rp.session, side: side, lots: lots });
    if (r.ok) { toast(r.msg, 'ok'); updateReplayUI(r); }
    else toast(r.msg || '操作失败', 'err');
  }
  async function rpFlat() {
    if (!rpAlive()) return;
    const r = await api('/api/replay/flat', { id: Q.rp.session });
    if (r.ok) { toast('已平仓', 'ok'); updateReplayUI(r); }
  }
  async function rpToggleAuto() {
    if (!rpAlive()) return;
    const r = await api('/api/replay/auto', { id: Q.rp.session, on: !Q.rp.auto });
    if (!r.ok) { toast(r.msg || '操作失败', 'err'); return; }
    toast(r.msg, 'ok');
    updateReplayUI(r);
    const btn = $id('rpAutoBtn');
    if (btn) {
      btn.textContent = r.auto ? '✋ 关闭自动跟随' : '🤖 自动跟随信号';
      btn.classList.toggle('primary', !r.auto);
    }
  }
  async function rpClear() {
    if (!rpAlive()) return;
    if (!confirm('清空本次复盘的所有操作记录？（行情回到区间起点，自动跟随可继续用）')) return;
    const r = await api('/api/replay/clear', { id: Q.rp.session });
    if (r.ok) { toast(r.msg, 'ok'); updateReplayUI(r); }
  }

  async function closeReplay() {
    if (!Q.rp || !Q.rp.session) return;
    if (Q.rpTimer) togglePlay();
    const r = await api('/api/replay/close', { id: Q.rp.session });
    if (!r.ok) { toast(r.msg || '结束失败', 'err'); return; }
    const st = r.stat || {};
    toast('复盘结束：已平仓 ' + (st.已平仓 || 0) + ' 笔，胜率 ' + fmt(st.胜率, 1) +
      '%，净盈亏 ' + fmt(st.净盈亏, 2) + '。记录仍可复制 / 导出', 'ok');
    // 只合并最新记录/统计，不重绘 —— 保住 K 线与资金曲线
    if (r.records) Q.rp.records = r.records;
    if (r.stat) Q.rp.stat = r.stat;
    Q.rp.session = '';                 // 会话已关闭
    const box = $id('rpRecBox');
    if (box) box.innerHTML = recTableHTML(Q.rp);
    const sub = document.querySelector('#rpRecCard .hd2 .desc');
    if (sub) {
      const groups = groupRecords(Q.rp.records || []);
      const closed = (Q.rp.records || []).filter(x => x.动作 === '平');
      sub.textContent = ((Q.rp.stat && Q.rp.stat['记录笔数']) || (Q.rp.records || []).length) +
        ' 条动作 · ' + groups.length + ' 笔交易 · 已平仓 ' + closed.length + ' 笔';
    }
    const ctrl = $id('rpCtrl');
    if (ctrl) {
      const closed = document.createElement('div');
      closed.className = 'rp-closed';
      closed.innerHTML = '✅ 本次复盘已结束 —— 上方的 K 线、资金变化曲线与下方记录都保留着，' +
        '<b>可展开查看每一笔的全部字段，可复制、可导出 CSV / TSV / JSON</b>。' +
        '想重新练一段，点左侧「开始复盘」即可。';
      ctrl.parentNode.insertBefore(closed, ctrl);
      ctrl.remove();
    }
    const range = $id('rpRange');
    if (range) range.disabled = true;
  }

  /* ==================================================================
     九、账户 · 银期转账
     ================================================================== */
  Q.acc = { info: null, dir: '转入' };

  function accBankLabel(b) {
    return b.bank + ' ' + b.cardNo + '（余额 ' + money(b.balance) + '）';
  }
  function accCurBank() {
    const sel = $id('aTBank');
    if (!sel) return null;
    const banks = (Q.acc.info && Q.acc.info.banks) || [];
    return banks.find(b => b.id === sel.value) || banks[0] || null;
  }
  function accUpdateTip() {
    const tip = $id('aTTip'); if (!tip) return;
    if (Q.acc.dir === '转入') {
      tip.innerHTML = '输入<b>银行交易密码</b>，资金从银行卡划入期货账户（结存增加）。';
    } else {
      tip.innerHTML = '转出需<b>银行交易密码</b> + <b>资金密码</b>双重验证，且不超过期货账户<b>可用资金</b>（占用保证金须先平仓）。';
    }
  }
  function accRender() {
    const info = Q.acc.info;
    if (!info) return;
    const cur = info.current;
    const acct = (S.state && S.state.account) || {};
    // KPI
    if ($id('aLogin')) {
      $id('aLogin').textContent = cur ? cur.loginName : '--';
      $id('aLoginX').textContent = cur ? '登录于 ' + (cur.createdAt || '') : '登录账号';
      $id('aTradeAcc').textContent = cur ? cur.tradeAcc : '--';
      $id('aEquity').textContent = acct.equity !== undefined ? money(acct.equity) : '--';
      $id('aAvail').textContent = acct.available !== undefined ? money(acct.available) : '--';
      const sum = (info.banks || []).reduce((a, b) => a + (b.balance || 0), 0);
      $id('aBankSum').textContent = money(sum);
      $id('aBankCnt').textContent = (info.banks || []).length + ' 张卡';
      $id('aCnt').textContent = (info.accounts || []).length;
    }
    // 头部徽标
    const pill = $id('accPill');
    if (pill) pill.innerHTML = '账户 <b>' + esc(cur ? cur.loginName : '--') + '</b>';
    // 账户下拉（登录 / 删除）
    const opts = (info.accounts || []).map(a =>
      '<option value="' + esc(a.id) + '"' + (cur && a.id === cur.id ? ' selected' : '') + '>' +
      esc(a.loginName) + (cur && a.id === cur.id ? '（当前）' : '') + '</option>').join('');
    if ($id('aSel')) $id('aSel').innerHTML = opts || '<option value="">（无账户）</option>';
    if ($id('aDelSel')) $id('aDelSel').innerHTML = opts || '<option value="">（无账户）</option>';
    // 银行下拉（新建首卡 / 加卡 / 转账）
    const banks = ['工商银行', '农业银行', '中国银行', '建设银行', '交通银行', '招商银行',
      '邮储银行', '中信银行', '兴业银行', '民生银行', '光大银行', '浦发银行'];
    const bankOpts = banks.map(b => '<option>' + b + '</option>').join('');
    if ($id('aNBank')) $id('aNBank').innerHTML = bankOpts;
    if ($id('abBank')) $id('abBank').innerHTML = bankOpts;
    const myBanks = info.banks || [];
    if ($id('aTBank')) $id('aTBank').innerHTML = myBanks.length
      ? myBanks.map(b => '<option value="' + esc(b.id) + '">' + esc(accBankLabel(b)) + '</option>').join('')
      : '<option value="">（尚未绑定银行卡）</option>';
    // 银行卡表
    const bt = $id('aBankTbl');
    if (bt) {
      if (!myBanks.length) {
        bt.innerHTML = '<tbody><tr><td class="empty">当前账户还没有银行卡，先在下方添加一张，才能银期转入。</td></tr></tbody>';
      } else {
        bt.innerHTML = '<thead><tr><th>银行</th><th>卡号</th><th>余额</th><th>绑定时间</th><th>操作</th></tr></thead><tbody>' +
          myBanks.map(b =>
            '<tr><td>' + esc(b.bank) + '</td><td class="mono">' + esc(b.cardNo) + '</td>' +
            '<td>' + money(b.balance) + '</td><td>' + esc(b.createdAt || '--') + '</td>' +
            '<td class="act"><button class="btn tiny danger" data-bdel="' + esc(b.id) + '" data-bname="' +
            esc(b.bank + ' ' + b.cardNo) + '">解绑</button></td></tr>').join('') + '</tbody>';
      }
    }
    // 转账流水（合并各卡 flow）
    const ft = $id('aFlowTbl');
    if (ft) {
      const flows = [];
      (myBanks).forEach(b => (b.flow || []).forEach(f => flows.push({ ...f, bank: b.bank + ' ' + b.cardNo })));
      flows.sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
      ft.innerHTML = flows.length
        ? '<thead><tr><th>时间</th><th>银行卡</th><th>类型</th><th>金额</th><th>卡内余额</th></tr></thead><tbody>' +
          flows.slice(0, 60).map(f =>
            '<tr><td class="mono">' + esc(f.ts || '') + '</td><td>' + esc(f.bank) + '</td>' +
            '<td class="' + (f.type === '银期转入' ? 'acc-in' : 'acc-out') + '">' + esc(f.type) + '</td>' +
            '<td class="' + (f.type === '银期转入' ? 'acc-in' : 'acc-out') + '">' +
            (f.type === '银期转入' ? '+' : '') + money(f.amount) + '</td>' +
            '<td>' + money(f.balanceAfter) + '</td></tr>').join('') + '</tbody>'
        : '<tbody><tr><td class="empty">暂无转账流水 —— 用「银期转账」卡 ↔ 期货账户划转后在这里留痕。</td></tr></tbody>';
    }
    accUpdateTip();
  }

  async function accLoad() {
    try {
      const r = await api('/api/acc/info');
      if (r && r.ok) { Q.acc.info = r; accRender(); }
    } catch (e) { /* 服务未就绪 */ }
  }

  function accBind() {
    // 方向切换
    const dirIn = $id('aDirIn'), dirOut = $id('aDirOut');
    if (dirIn && dirOut) {
      const setDir = d => {
        Q.acc.dir = d;
        dirIn.classList.toggle('on', d === '转入');
        dirOut.classList.toggle('on', d === '转出');
        $id('aTPwdRow').style.display = d === '转出' ? '' : 'none';
        $id('aTBankPwd').placeholder = '银行交易密码';
        accUpdateTip();
      };
      dirIn.addEventListener('click', () => setDir('转入'));
      dirOut.addEventListener('click', () => setDir('转出'));
      setDir('转入');
    }
    // 金额快捷
    document.querySelectorAll('[data-qa]').forEach(c => c.addEventListener('click', () => {
      $id('aTAmt').value = c.dataset.qa;
    }));
    // 登录 / 切换
    if ($id('aBtnLogin')) $id('aBtnLogin').addEventListener('click', async () => {
      const id = $id('aSel').value;
      const name = ((Q.acc.info && Q.acc.info.accounts) || []).find(a => a.id === id);
      const pwd = $id('aLoginPwd').value;
      if (!id) { toast('没有可登录的账户', 'err'); return; }
      if (!pwd) { toast('请输入登录密码', 'err'); return; }
      const r = await api('/api/acc/login', { loginName: name ? name.loginName : id, loginPwd: pwd });
      toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) { $id('aLoginPwd').value = ''; await accLoad(); await pollState(); }
    });
    // 新建账户
    if ($id('aBtnAdd')) $id('aBtnAdd').addEventListener('click', async () => {
      const loginName = $id('aNLogin').value.trim();
      const loginPwd = $id('aNLoginPwd').value;
      if (!loginName || !loginPwd) { toast('请填写登录账号与登录密码', 'err'); return; }
      const body = { loginName, loginPwd, tradePwd: $id('aNTradePwd').value };
      const bank = $id('aNBank').value, card = $id('aNCard').value.trim(), cpwd = $id('aNCardPwd').value;
      if (bank && card && cpwd) {
        body.firstBank = { bank, cardNo: card, cardPwd: cpwd, balance: num($id('aNBalance').value) || 0 };
      }
      const r = await api('/api/acc/add', body);
      toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) {
        ['aNLogin', 'aNLoginPwd', 'aNTradePwd', 'aNCard', 'aNCardPwd'].forEach(x => { $id(x).value = ''; });
        await accLoad(); await pollState();
      }
    });
    // 删除账户
    if ($id('aBtnDel')) $id('aBtnDel').addEventListener('click', async () => {
      const id = $id('aDelSel').value;
      const name = ((Q.acc.info && Q.acc.info.accounts) || []).find(a => a.id === id);
      const pwd = $id('aDelPwd').value;
      if (!id || !name) { toast('请选择要删除的账户', 'err'); return; }
      if (!pwd) { toast('请输入该账户的登录密码', 'err'); return; }
      if (!confirm('确认删除模拟账户「' + name.loginName + '」？\n该账户的持仓 / 资金 / 流水将被一并清除，不可恢复！')) return;
      const r = await api('/api/acc/delete', { id, loginPwd: pwd });
      toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) { $id('aDelPwd').value = ''; await accLoad(); await pollState(); }
    });
    // 加卡
    if ($id('aBtnBankAdd')) $id('aBtnBankAdd').addEventListener('click', async () => {
      const body = { bank: $id('abBank').value, cardNo: $id('abCard').value.trim(),
        cardPwd: $id('abPwd').value, balance: num($id('abBalance').value) || 0 };
      if (!body.cardNo || !body.cardPwd) { toast('请填写卡号与银行交易密码', 'err'); return; }
      const r = await api('/api/bank/add', body);
      toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) { ['abCard', 'abPwd'].forEach(x => { $id(x).value = ''; }); await accLoad(); }
    });
    // 解绑卡（事件委托）
    const bankTbl = $id('aBankTbl');
    if (bankTbl) bankTbl.addEventListener('click', async e => {
      const btn = e.target.closest('[data-bdel]');
      if (!btn) return;
      const pwd = prompt('解绑「' + btn.dataset.bname + '」需要输入该卡的银行交易密码：');
      if (pwd === null) return;
      const r = await api('/api/bank/delete', { id: btn.dataset.bdel, cardPwd: pwd });
      toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) await accLoad();
    });
    // 转账
    if ($id('aBtnTransfer')) $id('aBtnTransfer').addEventListener('click', async () => {
      const bank = accCurBank();
      if (!bank) { toast('请先绑定一张银行卡', 'err'); return; }
      const amt = num($id('aTAmt').value);
      if (!amt || amt <= 0) { toast('请输入转账金额', 'err'); return; }
      const bankPwd = $id('aTBankPwd').value;
      if (!bankPwd) { toast('请输入银行交易密码', 'err'); return; }
      const body = { direction: Q.acc.dir, bankId: bank.id, amount: amt, bankPwd };
      if (Q.acc.dir === '转出') {
        body.tradePwd = $id('aTTradePwd').value;
        if (!body.tradePwd) { toast('转出需要输入资金密码', 'err'); return; }
      }
      const r = await api('/api/acc/transfer', body);
      toast(r.msg, r.ok ? 'ok' : 'err');
      if (r.ok) {
        ['aTBankPwd', 'aTTradePwd'].forEach(x => { $id(x).value = ''; });
        await accLoad(); await pollState();
      }
    });
  }

  /* ==================================================================
     启动
     ================================================================== */
  async function init() {
    const wait = async (fn, ms = 12000) => {
      const t0 = Date.now();
      while (Date.now() - t0 < ms) { if (fn()) return true; await new Promise(r => setTimeout(r, 200)); }
      return false;
    };
    await wait(() => S.cfg);
    if (!S.cfg) { try { S.cfg = await api('/api/config'); } catch (e) { } }
    Q.cfg = S.cfg;
    Q.templates = (S.cfg && S.cfg['策略模板']) || [];
    Q.directions = (S.cfg && S.cfg['策略方向']) || ['双向', '仅做多', '仅做空'];
    Q.sectors = (S.cfg && S.cfg['板块顺序']) || [];
    const dd = (S.cfg && S.cfg['指标'] && S.cfg['指标']['默认主图']);
    if (dd && dd.length) Q.mainOn = [String(dd[0]).split('(')[0]];
    const sd = (S.cfg && S.cfg['指标'] && S.cfg['指标']['默认副图']);
    if (sd && sd.length) Q.subOn = sd.map(x => String(x).split('(')[0]).slice(0, 3);

    renderSeg();
    renderIndPickers();
    bindMarket();
    bindStrategy();
    buildStrategyForm(null);
    buildBacktestForm();
    buildReplayForm();
    accBind();
    accLoad();

    // 板块 chips
    const sc = $id('mkSector');
    if (sc) {
      sc.innerHTML = ['', ...Q.sectors].map(s =>
        '<span class="chip' + (s === Q.mkSector ? ' on' : '') + '" data-s="' + esc(s) + '">' +
        (s || '全部品种类型') + '</span>').join('');
      sc.addEventListener('click', e => {
        const c = e.target.closest('.chip'); if (!c) return;
        Q.mkSector = c.dataset.s;
        sc.querySelectorAll('.chip').forEach(x => x.classList.toggle('on', x === c));
        renderMarketList(true);
        loadChart();
      });
    }

    // 行情列表首屏
    await wait(() => (S.contracts || []).length > 0);
    await wait(() => Object.keys(S.quote || {}).length > 0, 8000);
    renderMarketList(true);
    const first = Q.mkRows[0];
    if (first) Q.mkSel = first.code;
    renderMarketList(true);
    loadChart();

    // 策略库
    try { await loadStrategies(); } catch (e) { }
    buildBacktestForm();   // 策略库就绪后重建，让「策略来源」下拉真正列出已保存策略

    // 定时刷新
    setInterval(() => { if (Q.page === 'market') renderMarketList(true); }, 5000);
    setInterval(() => { if (Q.page === 'strategy') loadStrategies(); }, 20000);
    setInterval(() => { if (Q.page === 'account') accLoad(); }, 5000);

    window.QT = { Q: Q, S: S, api: api, gotoPage: gotoPage, loadChart: loadChart,
      recExportText: recExportText };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => setTimeout(init, 60));
  } else {
    setTimeout(init, 60);
  }
})();
