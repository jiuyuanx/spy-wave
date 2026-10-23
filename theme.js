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

  // 热力区样式 (2026-10-23)
  heat: {
    zoneUpColor: '#ff9900',    // 7区上缘 橙色
    zoneLoColor: '#0277BD',    // 7区下缘 蓝色
    zoneLineWidth: 1.5,
    zoneLineType: 'solid',     // 7区边缘实线
    trigUpColor: '#FF6F00',    // 上15%触发线 橙色虚线
    trigLoColor: '#0277BD',    // 下15%触发线 蓝色虚线
    trigLineWidth: 1,
    trigLineType: 'dashed',
    barBorderColor: '#ffffff', // 7区内柱子白色边缘
    barBorderWidth: 1,
    signalBorderColor: '#ffffff', // 做T信号白色边缘
    signalBorderWidth: 2
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
        s += '<br/><span style="color:#ff9900">slope5 ' + ps[i].value + '</span>';
        break;
      }
    }
    if (b.acts && b.acts.length) s += '<br/>' + b.acts.join('<br/>');
    return s;
  }
};
