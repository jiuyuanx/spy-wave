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
      borderWidth: 2,
      shadowColor: 'rgba(255,255,255,0.5)', shadowBlur: 8
    }
  },

  // 9m QQE：蓝青/玫瑰红 + 玻璃光影 (2026-10-14)
  qqe9: {
    barWidth: '260%',
    up: { color: 'rgba(30,170,255,0.25)' },
    down: { color: 'rgba(255,130,180,0.25)' }
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

  // tooltip: only open / close / QQE (no high/low)
  tooltipFormatter: function(ps, barInfo) {
    var t = ps[0].axisValue;
    var b = barInfo[t];
    if (!b) return t;
    var s = '<b>' + t + '</b><br/>开' + b.o + ' 收' + b.c +
            '<br/>QQE ' + b.qqe + '(' + b.col + ')';
    if (b.acts && b.acts.length) s += '<br/>' + b.acts.join('<br/>');
    return s;
  },

  // 注入3m开关按钮 (2026-10-14) - 所有历史页面自动生效，无需重生成
  inject3mButtons: function() {
    // 如果已经有3mK按钮，说明新版生成器已包含，跳过
    if (document.getElementById('tg3mk')) return;
    var tg1m = document.getElementById('tg1m');
    if (!tg1m || !tg1m.parentNode) return;
    var parent = tg1m.parentNode;

    function mkBtn(id, label, seriesName, borderColor, bgColor) {
      var b = document.createElement('button');
      b.id = id;
      b.textContent = label;
      b.style.cssText = 'font-size:12px;padding:2px 7px;margin:0 2px;border:1px solid ' +
        borderColor + ';background:' + bgColor + ';border-radius:4px;cursor:pointer';
      b.onclick = function() { toggleSeries(seriesName); };
      return b;
    }

    // 在1m按钮前插入3mK，在9mK后插入3mQ
    var btn3mk = mkBtn('tg3mk', '3mK', '3mK线', '#666', '#f0f0f0');
    parent.insertBefore(btn3mk, tg1m);

    var tg9mk = document.getElementById('tg9mk');
    var btn3mq = mkBtn('tg3mq', '3mQ', 'QQE', '#888', '#f5f5f5');
    if (tg9mk && tg9mk.nextSibling) {
      parent.insertBefore(btn3mq, tg9mk.nextSibling);
    } else {
      parent.appendChild(btn3mq);
    }

    // 更新toggleSeries的id映射 (如果还是旧版)
    if (typeof toggleSeries === 'function') {
      var origToggle = toggleSeries;
      window.toggleSeries = function(name) {
        origToggle(name);
        var id = name==='1m曲线'?'tg1m':name==='9mK线'?'tg9mk':name==='9m QQE'?'tg9mq':name==='3mK线'?'tg3mk':'tg3mq';
        var btn = document.getElementById(id);
        if (btn) {
          btn.style.opacity = (btn.style.opacity==='0.35'?'1':'0.35');
        }
      };
    }
  }
};

// 页面加载后自动注入3m按钮
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', function() { THEME.inject3mButtons(); });
} else {
  THEME.inject3mButtons();
}
