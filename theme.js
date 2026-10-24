/* SPY Wave Dashboard shared theme (solve once for all).
 * All pages (index.html + YYYY-MM-DD.html) load this file.
 * Change colors / sizes / tooltip here once -> every page updates.
 */
var THEME = {
  // semantic signal type -> hex color
  colors: {
    flip_call:     '#005000',  // 翻C 深绿(少一点黑)
    flip_put:      '#a80000',  // 翻P 深红(少一点黑)
    follow_call:   '#006600',  // 跟C 深绿
    follow_put:    '#cc0000',  // 跟P 深红
    reenter_call:  '#00a0a0',  // 补C 青色
    reenter_put:   '#ff4500',  // 补P 橘红
    open_call:     '#66cc66',  // 开C 浅绿
    open_put:      '#ff6666',  // 开P 浅红
    close_call:    '#b27a7a',  // 平C 灰红
    close_put:     '#7ab27a',  // 平P 灰绿
    stoploss_call: '#b27a7a',  // 止损C 灰红
    stoploss_put:  '#7ab27a'   // 止损P 灰绿
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

  // tooltip style (2026-10-18 user's: 背景透明度统一在theme改)
  tooltip: {
    backgroundColor: 'rgba(255,255,255,0.6)',
    borderColor: 'rgba(0,0,0,0.15)',
    borderWidth: 1,
    textStyle: { color: '#333', fontSize: 12 }
  },

  // tooltip: only open / close / QQE (no high/low)
  tooltipFormatter: function(ps, barInfo) {
    var t = ps[0].axisValue;
    var b = barInfo[t];
    if (!b) return t;
    var s = '<b>' + t + '</b><br/>开' + b.o + ' 收' + b.c +
            '<br/>QQE ' + b.qqe + '(' + b.col + ')';
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
