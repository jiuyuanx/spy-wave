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
