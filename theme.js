/* SPY Wave Dashboard shared theme (solve once for all).
 * All pages (index.html + YYYY-MM-DD.html) load this file.
 * Change colors / sizes / tooltip here once -> every page updates.
 */
var THEME = {
  // 信号圈规范色 (2026-11-15 user令, solve once for all):
  // 开仓类(翻/跟/补/开)一律按方向取正绿/正红, 平仓/止损一律灰。
  // 改这里 -> 所有日页运行时经 applyColors 即时生效; gen 的烘焙色只是兜底, 已同步同值。
  pinSpec: {
    openCall: '#00cc00',  // 正绿 (开 call 类信号圈)
    openPut:  '#ff0000',  // 正红 (开 put 类信号圈)
    close:    '#9ca3af'   // 灰 (平仓/止损圈)
  },

  // slope 规范色 (2026-11-15 登记; 与日页 Envelope 整合版 build_daypage_env.py 同值)
  slope: {
    sky:       '#38bdf8',  // slope 段曲线染色 + slope 单信号圈描边
    dyeWidth:  3,          // 段内染色线宽
    closeFill: '#9ca3af',  // slope 平仓圈填充 (灰, 同 pinSpec.close)
    closeEdge: '#6b7280'   // 终栈平仓圈描边 (灰)
  },

  // semantic signal type -> hex color (值同 pinSpec, applyColors 按标签取这里)
  colors: {
    flip_call:     '#00cc00',  // 翻C 正绿
    flip_put:      '#ff0000',  // 翻P 正红
    follow_call:   '#00cc00',  // 跟C 正绿
    follow_put:    '#ff0000',  // 跟P 正红
    reenter_call:  '#00cc00',  // 补C 正绿
    reenter_put:   '#ff0000',  // 补P 正红
    open_call:     '#00cc00',  // 开C 正绿
    open_put:      '#ff0000',  // 开P 正红
    close_call:    '#9ca3af',  // 平C 灰
    close_put:     '#9ca3af',  // 平P 灰
    stoploss_call: '#9ca3af',  // 止损C 灰
    stoploss_put:  '#9ca3af'   // 止损P 灰
  },

  heat: {
    zoneUpColor: '#ff9900',
    zoneLoColor: '#0277BD',
    zoneLineWidth: 1.5,
    zoneLineType: 'solid',
  },
  markPoint: {
    symbolSize: 26,
    fontSize: 7,
    opacity: 0.75
  },

  // 9m K线：染色雾面玻璃 (user picked B, 2026-10-14)
  k9: {
    barWidth: '280%',
    style: {
      color: 'rgba(0,170,68,0.25)', color0: 'rgba(221,34,34,0.25)',
      borderColor: 'rgba(0,170,68,1)', borderColor0: 'rgba(221,34,34,1)',
      borderWidth: 1,
      shadowColor: 'rgba(255,255,255,0.5)', shadowBlur: 8
    }
  },

  // 3m QQE真色 (2026-10-18 user's: 统一进theme)
  qqe3: {
    up:   '#2f80ed',  // 真蓝
    down: '#e5484d',  // 真红
    gray: '#b0b0b0'   // 灰
  },

  // 9m QQE：天蓝/红 (2026-10-18 user's)
  qqe9: {
    barWidth: '260%',
    up: {
      color: 'rgba(30,170,255,0.25)',
      borderColor: 'rgba(30,170,255,0.7)', borderWidth: 1,
      shadowColor: 'rgba(255,255,255,0.5)', shadowBlur: 8
    },
    down: {
      color: 'rgba(229,72,77,0.25)',
      borderColor: 'rgba(229,72,77,0.7)', borderWidth: 1,
      shadowColor: 'rgba(255,255,255,0.5)', shadowBlur: 8
    }
  },

  // Envelope 轨道线 (2026-11-05 user's: 下轨改橘红, 统一进theme; solve once for all)
  envelope: {
    upper:   '#1a9e54',  // 上轨 绿
    lower:   '#f97316',  // 下轨 橙色
    chanmid: '#eab308',  // 中轨 黄
    mid:     '#888888',  // 均线 灰
    trendUp:   '#14b8a6',  // 趋势强涨 亮青色
    trendDown: '#b91c1c',  // 趋势强跌 红
    trendFlat: '#1e40af',  // 趋势中性 深蓝
    trendWidth: 4          // 趋势段线宽 (加粗)
  },

  // Chinese label (e.g. '翻C') -> semantic key
  keyFor: function(value) {
    var m = {
      '翻C': 'flip_call',     '翻P': 'flip_put',
      '跟C': 'follow_call',   '跟P': 'follow_put',
      '补C': 'reenter_call',  '补P': 'reenter_put',
      '开C': 'open_call',     '开P': 'open_put',
      '平C': 'close_call',    '平P': 'close_put',
      '止损C': 'stoploss_call', '止损P': 'stoploss_put'
    };
    return m[value] || null;
  },

  colorFor: function(value) {
    var k = this.keyFor(value);
    return k ? this.colors[k] : null;
  },

  // (re)apply theme colors to a spins/pins array in place, by value label
  applyColors: function(arr) {
    if (!arr) return arr;
    for (var i = 0; i < arr.length; i++) {
      var c = this.colorFor(arr[i].value);
      if (c) {
        arr[i].itemStyle = arr[i].itemStyle || {};
        arr[i].itemStyle.color = c;
      }
    }
    return arr;
  },

  // 每单盈亏背景alpha (2026-10-19 用户, 23:00四段折线版): 20%→0.4, 35%→0.5, 50%→0.6, 65%封顶0.7
  // 用法: THEME.pnlBgColor(pct) -> 'rgba(...)'，pct为数字百分比（如9.98表示+9.98%）
  // 改公式只改这里，所有引用此函数的页面自动生效（solve once for all）
  pnlAlpha: function(pct) {
    var a = Math.abs(pct);
    if (a <= 20) return 0.08 + a * 0.016;                     // 20% -> 0.40
    if (a <= 35) return 0.4 + (a - 20) * (0.1 / 15);          // 35% -> 0.50
    if (a <= 50) return 0.5 + (a - 35) * (0.1 / 15);          // 50% -> 0.60
    return Math.min(0.6 + (a - 50) * (0.1 / 15), 0.7);        // 65%封顶0.7
  },
  pnlBgColor: function(pct) {
    var a = this.pnlAlpha(pct).toFixed(3);
    return pct >= 0 ? 'rgba(0,170,0,' + a + ')' : 'rgba(230,0,0,' + a + ')';
  },

  // ST版标签 (2026-11-01 theme令, ST专用; QQE标签/带子一律不动, 不调这里)
  segLabel: {pos: '#66bb6a', neg: '#ef5350', fontSize: 8},
  tradeLabel: {pos: '#1a9e54', neg: '#e5484d', fontSize: 11},
  segLabelColor: function(pct) {
    return pct >= 0 ? this.segLabel.pos : this.segLabel.neg;
  },
  tradeLabelColor: function(pct) {
    return pct >= 0 ? this.tradeLabel.pos : this.tradeLabel.neg;
  },

  // ST版带子alpha (2026-11-01 theme令): |pct|<=10时 alpha=|pct|*0.024
  // (0%->0.000, 1%->0.024, 5%->0.120, 10%->0.240与原公式在10%处衔接);
  // 10%以上与原pnlAlpha一字不差 (20%->0.40, 35%->0.50, 50%->0.60, 65%+封顶0.70)
  pnlAlphaST: function(pct) {
    var a = Math.abs(pct);
    if (a <= 10) return a * 0.024;
    return this.pnlAlpha(pct);
  },
  pnlBgColorST: function(pct) {
    var a = this.pnlAlphaST(pct).toFixed(3);
    return pct >= 0 ? 'rgba(0,170,0,' + a + ')' : 'rgba(230,0,0,' + a + ')';
  },

  // tooltip style (2026-10-18 user's: 背景透明度统一在theme改)
  tooltip: {
    backgroundColor: 'rgba(255,255,255,0.6)',
    borderColor: 'rgba(0,0,0,0.15)',
    borderWidth: 1,
    textStyle: { color: '#333', fontSize: 12 }
  },

  // tooltip: only open / close / QQE (no high/low)
  // 2026-10-24: 加 7 区上下缘 + 在区内的 heat 值（backfill 做T版用，barInfo 需含 zu/zl/heat）
  tooltipFormatter: function(ps, barInfo) {
    var t = ps[0].axisValue;
    var b = barInfo[t];
    if (!b) return t;
    var s = '<b>' + t + '</b><br/>开' + b.o + ' 收' + b.c +
            '<br/>QQE ' + b.qqe + '(' + b.col + ')';
    if (b.zu != null && b.zl != null) {
      s += '<br/><span style="color:#ff9900">上缘 ' + Number(b.zu).toFixed(1) + '</span>' +
           ' <span style="color:#0277BD">下缘 ' + Number(b.zl).toFixed(1) + '</span>';
    }
    if (b.heat != null) {
      s += '<br/><span style="color:' + (b.heat >= 7 ? 'red' : 'black') + '">heat ' + Number(b.heat).toFixed(1) + '</span>';
    }
    for (var i = 0; i < ps.length; i++) {
      if (ps[i].seriesName === 'cumslope' && ps[i].value !== null && ps[i].value !== 'null') {
        s += '<br/><span style="color:#20b2aa">cumslope ' + ps[i].value + '</span>';
        break;
      }
    }
    for (var i = 0; i < ps.length; i++) {
      if (ps[i].seriesName === 'slope5' && ps[i].value !== null && ps[i].value !== 'null') {
        s += '<br/><span style="color:#ff69b4">slope5 ' + ps[i].value + '</span>';
        break;
      }
    }
    if (b.acts && b.acts.length) s += '<br/>' + b.acts.join('<br/>');
    return s;
  }
};

/* 热力15色 (2026-10-23, from gen_heat_html_pierce2.py; user-approved via heatmap_replay prototype).
 * Solve once for all: 实盘dashboard + backfill历史页共用。
 */
var HEAT_COLORS = ["rgba(120,180,220,0.12)", "rgba(138,182,201,0.16)", "rgba(156,185,182,0.19)", "rgba(174,188,164,0.23)", "rgba(192,190,145,0.27)", "rgba(210,193,126,0.30)", "rgba(228,196,108,0.34)", "rgba(246,198,89,0.38)", "rgba(255,186,74,0.41)", "rgba(255,160,64,0.45)", "rgba(255,133,53,0.49)", "rgba(255,106,42,0.52)", "rgba(255,79,31,0.56)", "rgba(255,53,21,0.60)", "rgba(255,26,10,0.63)"];

// v clamp 0-15 -> 15色
function heatColor(v) {
  var i = Math.max(0, Math.min(14, Math.floor(v)));
  return HEAT_COLORS[i];
}

// 热度标注文字颜色: 7以上红字, 7以下黑字 (user's 2026-10-23)
function heatLabelColor(v) {
  return v >= 7 ? 'red' : 'black';
}

// 从快照生成markArea热力色带 (snap: [[price, heat], ...], 只含heat>0.5的bin)
// 色带: yAxis price±0.25 (0.5 bin对齐); 右侧标注热度值, bold
function heatBandsFor(snap) {
  var bands = [];
  for (var k = 0; k < snap.length; k++) {
    var p = snap[k][0], v = snap[k][1];
    bands.push([
      {yAxis: Math.round((p - 0.25) * 100) / 100,
       itemStyle: {color: heatColor(v)},
       label: {show: true, position: 'insideRight', formatter: v.toFixed(1),
               color: heatLabelColor(v), fontSize: 10, fontWeight: 'bold'}},
      {yAxis: Math.round((p + 0.25) * 100) / 100}
    ]);
  }
  return bands;
}

// 热力重演: 绑定dataZoom事件, 拖动时按end位置重演对应时刻的heatmap
// ch: echarts实例; n: bar总数; snaps: 每根bar的快照[[price,heat],...];
// pnlBands: 静态P&L色带(xAxis); seriesName: K线series名('3mK线')
function bindHeatReplay(ch, n, snaps, pnlBands, seriesName) {
  function updateHeat(endIdx) {
    // snaps比n少2（前2根bar无快照），做映射
    var si = Math.max(0, Math.min(snaps.length - 1, endIdx - 2));
    var snap = (snaps.length > 0) ? snaps[si] : [];
    ch.setOption({series: [{name: seriesName,
      markArea: {silent: true, data: pnlBands.concat(heatBandsFor(snap))}}]});
  }
  ch.on('datazoom', function() {
    var dzs = ch.getOption().dataZoom || [];
    var dz = null;
    for (var i = 0; i < dzs.length; i++) {
      if (dzs[i].type === 'slider') { dz = dzs[i]; break; }
    }
    if (!dz && dzs.length) dz = dzs[0];
    if (!dz) return;
    var endIdx = Math.min(n - 1, Math.max(0, Math.floor(dz.end / 100 * n)));
    updateHeat(endIdx);
  });
  updateHeat(n - 1);
}
