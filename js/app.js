/* 시간대별 고속도로 소요시간 지도
 * 자료는 data/data.js (tools/build_data.py 가 만든다) 에서 읽는다.
 */
(function () {
  'use strict';

  var D = window.MAPDATA;
  if (!D || !window.d3) { document.body.classList.add('nodata'); return; }

  // ───────────────────────────────────────────── 설정
  var LOOP_SEC = 30;                 // 24시간을 몇 초에 보여 줄지
  var STEP = 30, MAX_MIN = 600;      // 색 띠 간격(분), 색이 끝나는 시간(분)
  var STAT_CITIES = ['대전', '강릉', '광주', '부산'];
  var FONT = '"Pretendard","Malgun Gothic","Apple SD Gothic Neo",sans-serif';
  // 지도 색: 바꾸고 싶으면 여기와 아래 색 띠(ramp)만 고치면 된다
  var SEA = '#f3f6fa', LAND = '#e1e7ee';          // 바다, 색을 칠하지 않는 땅
  var INK = '#16202e', INK2 = '#33404f';          // 지도 위 글자
  var HALO = 'rgba(255,255,255,.92)';             // 글자 테두리
  var ISO = 'rgba(255,255,255,.9)';               // 1시간 선
  var COAST = 'rgba(40,55,80,.38)';               // 해안선
  var NET = 'rgba(30,42,62,.26)';                 // 전체 고속도로
  var LINK_CASE = 'rgba(255,255,255,.8)', LINK = 'rgba(22,32,46,.88)';   // 자료가 있는 구간

  var thresholds = d3.range(STEP, MAX_MIN + 1, STEP);              // 30, 60, … 600
  var ramp = d3.interpolateRgbBasis([                              // 가까움(밝음) → 멂(어두움)
    '#fffbe0', '#fff0ae', '#ffdd7d', '#ffc45f', '#ffa65b', '#fb8862', '#f06d73',
    '#de5a89', '#c44f9f', '#a24eb1', '#7f53bb', '#6060c2', '#4a6fc4']);
  var colors = d3.range(thresholds.length + 1).map(function (i) { return ramp(i / thresholds.length); });

  // ───────────────────────────────────────────── 자료 풀기
  var g = D.grid, N = g.w * g.h;
  function decodeGrid(b64) {
    var bin = atob(b64), u8 = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    var u16 = new Uint16Array(u8.buffer);
    return d3.range(24).map(function (h) { return u16.subarray(h * N, (h + 1) * N); });
  }
  var cur = new Float32Array(N);
  var contours = d3.contours().size([g.w, g.h]).thresholds(thresholds);
  var polys = [];

  function ringsPath(rings) {
    var p = new Path2D();
    rings.forEach(function (r) {
      p.moveTo(r[0], r[1]);
      for (var i = 2; i < r.length; i += 2) p.lineTo(r[i], r[i + 1]);
      p.closePath();
    });
    return p;
  }
  function linesPath(lines) {
    var p = new Path2D();
    lines.forEach(function (r) {
      p.moveTo(r[0], r[1]);
      for (var i = 2; i < r.length; i += 2) p.lineTo(r[i], r[i + 1]);
    });
    return p;
  }
  var fieldPath = ringsPath(D.land.field);
  var dimPath = ringsPath(D.land.kr.concat(D.land.other));
  var netPath = linesPath(D.net);
  var hit = document.createElement('canvas').getContext('2d');      // 육지 안인지 확인용

  // 처음 보여 줄 범위: 본토 + 제주
  var focus = (function () {
    var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    D.land.field.concat(D.land.kr).forEach(function (r) {
      for (var i = 0; i < r.length; i += 2) {
        x0 = Math.min(x0, r[i]); x1 = Math.max(x1, r[i]);
        y0 = Math.min(y0, r[i + 1]); y1 = Math.max(y1, r[i + 1]);
      }
    });
    return [Math.max(x0, g.x0 - 4), y0, x1, y1];
  })();

  // ───────────────────────────────────────────── 요소
  var $ = function (id) { return document.getElementById(id); };
  var wrap = $('mapwrap'), canvas = $('map'), ctx = canvas.getContext('2d');
  var tip = $('tip'), resetBtn = $('reset'), hourEl = $('hour');
  var ticker = $('ticker'), ticksEl = $('ticks'), fillEl = $('fill');
  var playBtn = $('play'), playIcon = $('playIcon'), playText = $('playText');
  var dirBtns = document.querySelectorAll('[data-dir]');

  var W = 0, H = 0, dpr = 1, fitK = 1;
  var view = d3.zoomIdentity, userMoved = false;
  var t = 0, playing = false, timeDirty = true, raf = 0, last = 0;
  var pointer = null, hoverLink = -1;
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ───────────────────────────────────────────── 글자
  function hourName(h) {
    if (h === 12) return '낮 12시';
    return (h < 12 ? '오전 ' : '오후 ') + (h < 12 ? h : h - 12) + '시';
  }
  function fmt(min) {
    var m = Math.round(min), h = Math.floor(m / 60), r = m % 60;
    if (!h) return r + '분';
    return h + '시간' + (r ? ' ' + r + '분' : '');
  }
  function lerpAt(arr) {            // 시간대 사이 값 잇기
    var h0 = Math.floor(t) % 24, h1 = (h0 + 1) % 24, f = t - Math.floor(t);
    return arr[h0] + (arr[h1] - arr[h0]) * f;
  }
  function bandColor(min) { return colors[Math.min(colors.length - 1, Math.floor(min / STEP))]; }

  // ───────────────────────────────────────────── 시간대 줄, 표, 범례
  d3.range(24).forEach(function (h) {
    var s = document.createElement('span');
    s.textContent = h;
    ticksEl.appendChild(s);
  });
  var tickEls = ticksEl.children;

  // ───────────────────────────────────────────── 방향별 자료 (지방방향 out / 서울방향 in)
  var DIR_TEXT = {
    out: {
      label: '지방방향',
      lede: '이 시간대 속도로 달릴 때 수도권 요금소에서 각 지점까지 걸리는 시간입니다.',
      place: '도착지', link: ' 이 구간 끝까지',
      note: '지방방향은 수도권 요금소에서 출발해 경로 순서대로 구간 소요시간을 더한 값입니다.'
    },
    'in': {
      label: '서울방향',
      lede: '이 시간대 속도로 달릴 때 각 지점에서 수도권 요금소까지 걸리는 시간입니다.',
      place: '출발지', link: ' 이 구간 시작에서 요금소까지',
      note: '서울방향은 경로 역순으로 구간 소요시간을 더한 값입니다. 각 지점에서 수도권 요금소까지 남은 시간이라 경로 첫 구간이 가장 큽니다.'
    }
  };
  var LABEL_FIRST = ['부산', '광주', '강릉', '대전', '대구', '목포', '천안', '원주', '울산', '춘천', '양양'];
  var SW = 84, SH = 24;
  var dirCache = {}, A = null;

  function prepare(key) {
    if (dirCache[key]) return dirCache[key];
    var src = D.dirs[key], o = { key: key, text: DIR_TEXT[key], links: src.links, cities: src.cities, origins: src.origins };
    o.hours = decodeGrid(src.grid);
    o.linkPath = linesPath(src.links.map(function (l) { return l.p; }));
    o.linkPaths = src.links.map(function (l) { return linesPath([l.p]); });
    o.cityByName = {};
    src.cities.forEach(function (c) { o.cityByName[c.name] = c; });
    o.cityOrder = src.cities.slice().sort(function (a, b) {
      var i = LABEL_FIRST.indexOf(a.name), j = LABEL_FIRST.indexOf(b.name);
      return (i < 0 ? 99 : i) - (j < 0 ? 99 : j);
    });
    return (dirCache[key] = o);
  }

  function buildStats() {
    var names = STAT_CITIES.filter(function (n) { return A.cityByName[n]; });
    // 두 방향을 같은 눈금으로 그려서 방향을 바꿔도 비교할 수 있게 한다
    A.statMax = d3.max(Object.keys(D.dirs), function (k) {
      return d3.max(D.dirs[k].cities, function (c) { return names.indexOf(c.name) >= 0 ? d3.max(c.T) : 0; });
    });
    var body = $('stats').tBodies[0];
    body.innerHTML = '';
    $('stats').tHead.rows[0].cells[0].textContent = A.text.place;
    A.statRows = names.map(function (n) {
      var c = A.cityByName[n], tr = document.createElement('tr');
      var path = c.T.map(function (v, h) {
        return (h ? 'L' : 'M') + (h / 23 * SW).toFixed(1) + ',' + (SH - 2 - v / A.statMax * (SH - 4)).toFixed(1);
      }).join('');
      tr.innerHTML = '<td><span class="sw"></span>' + n + '</td><td></td><td></td>' +
        '<td><svg width="' + SW + '" height="' + SH + '" viewBox="0 0 ' + SW + ' ' + SH + '" role="img" aria-label="' +
        n + ' 하루 변화"><path d="' + path + '"/><circle r="3"/></svg></td>';
      body.appendChild(tr);
      return { c: c, min: d3.min(c.T), sw: tr.querySelector('.sw'), time: tr.cells[1], diff: tr.cells[2], dot: tr.querySelector('circle') };
    });
  }

  function buildNotes() {
    var gates = A.origins.map(function (o) { return o.name; }).join(', ');
    $('notes').innerHTML = [
      '자료: ' + D.meta.source + ' 의 첫 번째 시트. ' + Object.keys(D.dirs).map(function (k) {
        return DIR_TEXT[k].label + ' 경로 ' + D.dirs[k].paths + '개, 구간 ' + D.dirs[k].links.length + '개';
      }).join(' / ') + '입니다.',
      A.text.note + ' 수도권 요금소는 ' + gates + ' 입니다.',
      '여러 경로가 같은 구간을 지나면 그중 ' + D.meta.agg + '값을 씁니다.',
      '고속도로 위의 색은 자료 값 그대로입니다. 고속도로에서 떨어진 곳은 가까운 구간까지 직선거리를 시속 ' + D.meta.offSpeed + 'km로 간다고 보고 시간을 더한 추정값입니다.',
      '재생 중에는 앞뒤 시간대 값을 이어서 보여 주고, 멈추면 정시 값을 보여 줍니다.',
      '지도에 적힌 도시 시간은 각 경로의 지방 쪽 끝 지점 기준입니다(부산은 대동TG, 춘천은 남춘천IC).',
      '도로망은 roadplus SHP, 해안선은 Natural Earth 자료입니다.'
    ].map(function (s) { return '<li>' + s + '</li>'; }).join('');
  }

  function setDir(key, quiet) {
    if (!D.dirs[key]) key = Object.keys(D.dirs)[0];
    A = prepare(key);
    hoverLink = -1;
    buildStats();
    buildNotes();
    $('lede').textContent = A.text.lede;
    Array.prototype.forEach.call(dirBtns, function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-dir') === key); });
    if (!quiet) {
      try { history.replaceState(null, '', '#' + key); } catch (e) { /* file:// 에서는 그냥 넘어간다 */ }
      timeDirty = true;
      schedule();
    }
  }

  var legend = $('legend'), legendMark;
  (function () {
    var seg = window.matchMedia('(max-width: 880px)').matches ? 7 : 12, every = seg < 10 ? 2 : 1;
    var html = '<div class="cap">소요시간</div><div class="bar" style="height:' + seg * colors.length + 'px">';
    colors.forEach(function (c) { html += '<i style="background:' + c + '"></i>'; });
    html += '</div><div class="lab">';
    for (var h = 0; h <= MAX_MIN / 60; h += every) {
      html += '<span style="top:' + (h * 60 / STEP / colors.length * 100) + '%">' + (h ? h + '시간' : '0') + '</span>';
    }
    html += '<i class="mark"></i></div>';
    legend.innerHTML = html;
    legendMark = legend.querySelector('.mark');
    legendMark.style.left = '-22px';
  })();

  // ───────────────────────────────────────────── 시간 갱신
  function updateField() {
    var h0 = Math.floor(t) % 24, h1 = (h0 + 1) % 24, f = t - Math.floor(t);
    var a = A.hours[h0], b = A.hours[h1], s = g.scale;
    if (f < 1e-6) { for (var i = 0; i < N; i++) cur[i] = a[i] * s; }
    else { for (var j = 0; j < N; j++) cur[j] = (a[j] + (b[j] - a[j]) * f) * s; }
    polys = contours(cur);
  }
  function updateUI() {
    var h = Math.round(t) % 24;
    hourEl.textContent = hourName(h);
    for (var i = 0; i < 24; i++) {
      var gap = Math.min((i - h + 24) % 24, (h - i + 24) % 24);
      tickEls[i].className = gap === 0 ? 'on' : gap === 1 ? 'near' : (i % 3 ? 'minor' : '');
    }
    fillEl.style.width = ((t + 0.5) % 24) / 24 * 100 + '%';
    ticker.setAttribute('aria-valuenow', h);
    ticker.setAttribute('aria-valuetext', hourName(h));
    A.statRows.forEach(function (r) {
      var v = lerpAt(r.c.T), d = v - r.min;
      r.sw.style.background = bandColor(v);
      r.time.textContent = fmt(v);
      r.diff.textContent = d < 1 ? '가장 빠름' : '+' + fmt(d);
      r.dot.setAttribute('cx', (Math.min(t, 23) / 23 * SW).toFixed(1));
      r.dot.setAttribute('cy', (SH - 2 - v / A.statMax * (SH - 4)).toFixed(1));
    });
  }

  // ───────────────────────────────────────────── 격자 값 읽기
  function sample(wx, wy) {
    var gx = wx - g.x0 - 0.5, gy = wy - g.y0 - 0.5;
    if (gx < 0 || gy < 0 || gx > g.w - 1 || gy > g.h - 1) return null;
    var x0 = Math.min(g.w - 2, Math.floor(gx)), y0 = Math.min(g.h - 2, Math.floor(gy));
    var fx = gx - x0, fy = gy - y0, k = y0 * g.w + x0;
    return (cur[k] * (1 - fx) + cur[k + 1] * fx) * (1 - fy) + (cur[k + g.w] * (1 - fx) + cur[k + g.w + 1] * fx) * fy;
  }
  function onLand(wx, wy) { return hit.isPointInPath(fieldPath, wx, wy); }

  function nearestLink(wx, wy, maxD) {
    var best = -1, bd = maxD * maxD;
    for (var n = 0; n < A.links.length; n++) {
      var p = A.links[n].p;
      for (var i = 0; i + 3 < p.length; i += 2) {
        var ax = p[i], ay = p[i + 1], bx = p[i + 2] - ax, by = p[i + 3] - ay;
        var l2 = bx * bx + by * by, u = l2 ? ((wx - ax) * bx + (wy - ay) * by) / l2 : 0;
        u = u < 0 ? 0 : u > 1 ? 1 : u;
        var dx = ax + bx * u - wx, dy = ay + by * u - wy, d2 = dx * dx + dy * dy;
        if (d2 < bd) { bd = d2; best = n; }
      }
    }
    return best;
  }

  // ───────────────────────────────────────────── 그리기
  function trace(mp) {
    ctx.beginPath();
    for (var a = 0; a < mp.coordinates.length; a++) {
      var poly = mp.coordinates[a];
      for (var b = 0; b < poly.length; b++) {
        var r = poly[b];
        ctx.moveTo(r[0][0], r[0][1]);
        for (var i = 1; i < r.length; i++) ctx.lineTo(r[i][0], r[i][1]);
        ctx.closePath();
      }
    }
  }
  function label(text, x, y, opt) {
    ctx.font = (opt.weight || 500) + ' ' + (opt.size || 12) + 'px ' + FONT;
    ctx.textAlign = opt.align || 'left';
    ctx.textBaseline = 'middle';
    ctx.lineJoin = 'round';
    ctx.lineWidth = 3.5;
    ctx.strokeStyle = HALO;
    ctx.strokeText(text, x, y);
    ctx.fillStyle = opt.color || INK;
    ctx.fillText(text, x, y);
    return ctx.measureText(text).width;
  }

  function draw() {
    var k = view.k, tx = view.x, ty = view.y;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = SEA;
    ctx.fillRect(0, 0, W, H);

    ctx.setTransform(dpr * k, 0, 0, dpr * k, dpr * tx, dpr * ty);
    ctx.fillStyle = LAND;
    ctx.fill(dimPath);
    ctx.fill(fieldPath);

    // 소요시간 색 띠: 육지 안쪽에만
    ctx.save();
    ctx.clip(fieldPath);
    ctx.beginPath();
    ctx.rect(g.x0, g.y0, g.w, g.h);
    ctx.clip();
    ctx.fillStyle = colors[0];
    ctx.fillRect(g.x0, g.y0, g.w, g.h);
    ctx.translate(g.x0, g.y0);
    for (var i = 0; i < polys.length; i++) {
      trace(polys[i]);
      ctx.fillStyle = colors[i + 1];
      ctx.fill('evenodd');
    }
    ctx.lineWidth = 1.2 / k;
    ctx.strokeStyle = ISO;
    for (var j = 0; j < polys.length; j++) {            // 1시간마다 흰 선
      if (polys[j].value % 60) continue;
      trace(polys[j]);
      ctx.stroke();
    }
    ctx.restore();

    ctx.lineJoin = ctx.lineCap = 'round';
    ctx.lineWidth = 0.8 / k;
    ctx.strokeStyle = COAST;
    ctx.stroke(fieldPath);

    // 도로망: 전체 고속도로(옅게) + 자료가 있는 구간(진하게)
    ctx.lineWidth = 0.8 / k; ctx.strokeStyle = NET; ctx.stroke(netPath);
    ctx.lineWidth = 3.4 / k; ctx.strokeStyle = LINK_CASE; ctx.stroke(A.linkPath);
    ctx.lineWidth = 1.4 / k; ctx.strokeStyle = LINK; ctx.stroke(A.linkPath);
    if (hoverLink >= 0) {
      ctx.lineWidth = 7 / k; ctx.strokeStyle = '#fff'; ctx.stroke(A.linkPaths[hoverLink]);
      ctx.lineWidth = 3.6 / k; ctx.strokeStyle = INK; ctx.stroke(A.linkPaths[hoverLink]);
    }

    // 여기부터 화면 좌표
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    var boxes = [];
    var zoomed = k > fitK * 1.9;
    A.origins.forEach(function (o) {
      var x = o.x * k + tx, y = o.y * k + ty;
      ctx.beginPath(); ctx.arc(x, y, 3.6, 0, 6.2832);
      ctx.fillStyle = '#fff'; ctx.fill();
      ctx.lineWidth = 1.8; ctx.strokeStyle = INK; ctx.stroke();
      if (zoomed) label(o.name, x + 8, y, { size: 11.5 });
    });
    function free(b) {
      return b[0] > 2 && b[2] < W - 2 && !boxes.some(function (o) { return b[0] < o[2] && b[2] > o[0] && b[1] < o[3] && b[3] > o[1]; });
    }
    var pts = A.cityOrder.map(function (c) { return { c: c, x: c.x * k + tx, y: c.y * k + ty }; })
      .filter(function (p) { return p.x > -20 && p.y > -20 && p.x < W + 20 && p.y < H + 20; });
    pts.forEach(function (p) { boxes.push([p.x - 5, p.y - 5, p.x + 5, p.y + 5]); });
    pts.forEach(function (p) {
      var name = p.c.name, time = fmt(lerpAt(p.c.T));
      ctx.font = '700 12.5px ' + FONT;
      var wn = ctx.measureText(name + ' ').width, w0 = ctx.measureText(name).width;
      ctx.font = '500 12.5px ' + FONT;
      var wt = ctx.measureText(time).width;
      ctx.beginPath(); ctx.arc(p.x, p.y, 3.2, 0, 6.2832);
      ctx.fillStyle = INK; ctx.fill();
      ctx.lineWidth = 1.6; ctx.strokeStyle = '#fff'; ctx.stroke();
      // 자리를 차례로 찾아본다: 오른쪽, 왼쪽, 아래, 위. 그래도 겹치면 이름만, 그래도 안 되면 점만.
      var tries = [[wn + wt, true], [w0, false]], done = false;
      for (var a = 0; a < tries.length && !done; a++) {
        var tw = tries[a][0];
        var spots = [[p.x + 8, p.y], [p.x - 8 - tw, p.y], [p.x + 6, p.y + 13], [p.x + 6, p.y - 13],
                     [p.x - 6 - tw, p.y + 13], [p.x - 6 - tw, p.y - 13]];
        for (var q = 0; q < spots.length && !done; q++) {
          var bx = [spots[q][0] - 2, spots[q][1] - 8, spots[q][0] + tw + 2, spots[q][1] + 8];
          if (!free(bx)) continue;
          label(name, spots[q][0], spots[q][1], { weight: 700, size: 12.5 });
          if (tries[a][1]) label(time, spots[q][0] + wn, spots[q][1], { weight: 500, size: 12.5, color: INK2 });
          boxes.push(bx);
          done = true;
        }
      }
    });

    // 1시간 선 이름표: 서울TG → 부산 방향 직선 위에서 선과 만나는 곳
    var o = A.origins[0], e = A.cityByName['부산'];
    if (o && e) {
      var prev = null, next = 60, M = 260;
      for (var s = 0; s <= M && next <= MAX_MIN; s++) {
        var wx = o.x + (e.x - o.x) * s / M, wy = o.y + (e.y - o.y) * s / M;
        var v = sample(wx, wy);
        if (v == null) { prev = null; continue; }
        if (prev != null && prev.v < next && v >= next) {
          var f = (next - prev.v) / (v - prev.v);
          var lx = (prev.x + (wx - prev.x) * f) * k + tx, ly = (prev.y + (wy - prev.y) * f) * k + ty;
          if (free([lx - 22, ly - 8, lx + 22, ly + 8]) && onLand((lx - tx) / k, (ly - ty) / k)) {
            label(next / 60 + '시간', lx, ly, { size: 11, weight: 600, align: 'center', color: INK2 });
            boxes.push([lx - 22, ly - 8, lx + 22, ly + 8]);
          }
          while (next <= v) next += 60;
        }
        prev = { v: v, x: wx, y: wy };
      }
    }
  }

  // ───────────────────────────────────────────── 지도 위 설명 상자
  function updateTip() {
    if (!pointer) { tip.hidden = true; legendMark.style.display = 'none'; hoverLink = -1; return; }
    var wx = (pointer.x - view.x) / view.k, wy = (pointer.y - view.y) / view.k;
    var n = nearestLink(wx, wy, 9 / view.k);
    var v = sample(wx, wy);
    if (n < 0 && (v == null || !onLand(wx, wy))) { tip.hidden = true; legendMark.style.display = 'none'; hoverLink = -1; return; }
    var html, shown;
    if (n >= 0) {
      var L = A.links[n];
      shown = lerpAt(L.T);
      html = '<b>' + fmt(shown) + '</b>' + L.road + A.text.link +
        '<div class="road">구간 속도 ' + Math.round(lerpAt(L.v)) + 'km/h, 길이 ' + L.km + 'km<br>구간 번호 ' + L.id + '</div>';
    } else {
      shown = v;
      html = '<b>약 ' + fmt(v) + '</b>가까운 고속도로 구간을 기준으로 한 추정값';
    }
    hoverLink = n;
    tip.innerHTML = html;
    tip.hidden = false;
    var tw = tip.offsetWidth, th = tip.offsetHeight;
    tip.style.left = Math.max(6, pointer.x + 16 + tw > W ? pointer.x - 16 - tw : pointer.x + 16) + 'px';
    tip.style.top = Math.max(6, pointer.y + 16 + th > H ? pointer.y - 12 - th : pointer.y + 16) + 'px';
    legendMark.style.display = 'block';
    legendMark.style.top = Math.min(1, shown / (STEP * colors.length)) * 100 + '%';
  }

  // ───────────────────────────────────────────── 한 장면
  function frame(ts) {
    raf = 0;
    if (playing) {
      var dt = Math.max(0, Math.min(ts - last, 100));
      last = ts;
      t = (t + dt / 1000 * 24 / LOOP_SEC) % 24;
      timeDirty = true;
    }
    if (timeDirty) { updateField(); updateUI(); timeDirty = false; }
    var before = hoverLink;
    updateTip();
    draw();
    if (before !== hoverLink && !playing) draw();
    if (playing) schedule();
  }
  function schedule() { if (!raf) raf = requestAnimationFrame(frame); }
  function setTime(v) { t = ((v % 24) + 24) % 24; timeDirty = true; schedule(); }

  function setPlaying(on) {
    playing = on;
    playBtn.setAttribute('aria-pressed', on);
    playText.textContent = on ? '멈춤' : '재생';
    playIcon.setAttribute('d', on ? 'M4 2.5h3v11H4zM9 2.5h3v11H9z' : 'M4 2.5v11l9-5.5z');
    if (on) { last = performance.now(); schedule(); }
    else setTime(Math.round(t));                       // 멈추면 정시 값으로 맞춘다
  }
  playBtn.addEventListener('click', function () { setPlaying(!playing); });

  // 시간대 줄: 누르거나 끌어서 이동
  var dragging = false;
  function scrub(ev) {
    var r = ticker.getBoundingClientRect();
    var x = (ev.clientX - r.left) / r.width * 24 - 0.5;
    setTime(Math.max(0, Math.min(23, x)));
  }
  ticker.addEventListener('pointerdown', function (ev) {
    dragging = true;
    ticker.setPointerCapture(ev.pointerId);
    if (playing) setPlaying(false);
    scrub(ev);
  });
  ticker.addEventListener('pointermove', function (ev) { if (dragging) scrub(ev); });
  ['pointerup', 'pointercancel'].forEach(function (n) {
    ticker.addEventListener(n, function () { if (dragging) { dragging = false; setTime(Math.round(t)); } });
  });
  function step(d) { if (playing) setPlaying(false); setTime(Math.round(t) + d); }
  ticker.addEventListener('keydown', function (ev) {
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowUp') { step(1); ev.preventDefault(); }
    else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowDown') { step(-1); ev.preventDefault(); }
    else if (ev.key === 'Home') { step(-Math.round(t)); ev.preventDefault(); }
    else if (ev.key === 'End') { step(23 - Math.round(t)); ev.preventDefault(); }
  });
  document.addEventListener('keydown', function (ev) {
    var tag = ev.target.tagName;
    if (tag === 'BUTTON' || tag === 'SUMMARY' || tag === 'INPUT' || ev.target === ticker) return;
    if (ev.key === ' ') { setPlaying(!playing); ev.preventDefault(); }
    else if (ev.key === 'ArrowRight') step(1);
    else if (ev.key === 'ArrowLeft') step(-1);
  });

  // ───────────────────────────────────────────── 이동·확대
  var sel = d3.select(canvas);
  var zoom = d3.zoom()
    .translateExtent([[-30, -30], [D.meta.width + 30, D.meta.height + 30]])
    .on('zoom', function (ev) {
      view = ev.transform;
      if (ev.sourceEvent) { userMoved = true; resetBtn.hidden = false; }
      schedule();
    });
  sel.call(zoom);

  function fitTransform() {
    var fw = focus[2] - focus[0], fh = focus[3] - focus[1];
    var k = Math.min(W / fw, H / fh) * 0.93;
    return d3.zoomIdentity.translate(W / 2 - k * (focus[0] + fw / 2), H / 2 - k * (focus[1] + fh / 2)).scale(k);
  }
  function resize() {
    var r = wrap.getBoundingClientRect();
    if (!r.width || !r.height) return;
    W = r.width; H = r.height;
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(W * dpr);
    canvas.height = Math.round(H * dpr);
    var fit = fitTransform();
    fitK = fit.k;
    zoom.scaleExtent([fitK * 0.8, fitK * 14]).extent([[0, 0], [W, H]]);
    if (!userMoved) sel.call(zoom.transform, fit);
    schedule();
  }
  resetBtn.addEventListener('click', function () {
    userMoved = false;
    resetBtn.hidden = true;
    (reduceMotion ? sel : sel.transition().duration(450)).call(zoom.transform, fitTransform());
  });
  if (window.ResizeObserver) new ResizeObserver(resize).observe(wrap);
  window.addEventListener('resize', resize);

  function setPointer(ev) {
    var r = canvas.getBoundingClientRect();
    pointer = { x: ev.clientX - r.left, y: ev.clientY - r.top };
    schedule();
  }
  canvas.addEventListener('pointermove', setPointer);
  canvas.addEventListener('pointerdown', setPointer);
  canvas.addEventListener('pointerleave', function (ev) {
    if (ev.pointerType === 'touch') return;             // 손가락을 떼도 설명은 남겨 둔다
    pointer = null; schedule();
  });

  // ───────────────────────────────────────────── 시작
  Array.prototype.forEach.call(dirBtns, function (b) {
    if (!D.dirs[b.getAttribute('data-dir')]) b.hidden = true;
    b.addEventListener('click', function () { setDir(b.getAttribute('data-dir')); });
  });
  setDir((location.hash || '').replace('#', '') || 'out', true);
  resize();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedule);
  if (reduceMotion) setTime(8); else setPlaying(true);
})();
