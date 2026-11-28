// S&P 500 악재 낙폭 검색기
const $ = id => document.getElementById(id);
const D = {}; let oldLoaded = false; let SHOCKS = [];
const esc = t => String(t ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const BASE = location.protocol === 'file:' ? 'https://gohjunwon.github.io/sp500-dip/' : '';
const fetchJSON = f => fetch(BASE + 'data/' + f + '?t=' + Date.now(), { cache: 'no-store' }).then(r => { if (!r.ok) throw f; return r.json() });
const pct = (a, b) => (a / b - 1) * 100;
const fmt = (v, d = 1) => (v > 0 ? '+' : '') + v.toFixed(d) + '%';
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x.toISOString().slice(0, 10) };
const dayDiff = (a, b) => Math.round((new Date(b) - new Date(a)) / 864e5);
const median = a => { const s = a.filter(x => x != null && isFinite(x)).sort((x, y) => x - y); return s.length ? s[Math.floor(s.length / 2)] : null };
const C = id => $(id).checked, V = id => +$(id).value;

async function init() {
  try {
    const shocks = await fetchJSON('shocks.json').catch(() => []); SHOCKS = shocks;
    $('presets').innerHTML = shocks.length ? shocks.slice(0, 6).map(x => `<button data-d="${x.d}" title="${esc(x.who)} ${x.dd}%">${esc(x.name)} (${x.d.slice(0, 7).replace('-', '.')})</button>`).join('') : '<span class="note">악재 목록 없음</span>';
    document.querySelectorAll('#presets button').forEach(b => b.onclick = () => { $('shock').value = b.dataset.d; run() });
    if (!$('shock').value && shocks.length) $('shock').value = shocks[0].d;
    const [meta, pr, fund, est, upd] = await Promise.all(['meta.json', 'prices_recent.json', 'fund.json', 'est.json', 'updated.json'].map(fetchJSON));
    Object.assign(D, { meta, fund, est, dates: pr.dates, p: pr.p, updT: upd.t });
    $('upd').textContent = '갱신 ' + new Date(upd.t).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    const opt = f => fetchJSON(f).catch(() => null);
    [D.risk, D.liq, D.macro, D.live, D.model, D.div] = await Promise.all([opt('risk.json'), opt('liq.json'), opt('macro.json'), opt('live.json'), opt('model.json'), opt('div.json')]);
    try { if (D.model && D.model.thr) { const sel = document.getElementById('vScore'); for (const o of sel.options) { const t = D.model.thr[o.value]; if (!t || o.value === '0') continue; o.textContent = `${o.value === '4' ? '4점만' : o.value + '점 이상'} (코로나 제외 ${t.n}건: 1년 뒤 상승 ${Math.round(t.u * 100)}%, 최고가 회복 ${Math.round(t.p * 100)}%${o.value === '3' ? ', 추천' : ''}${t.n < 30 ? ', 표본 적음' : ''})` } } } catch (e) { }
    D.risk = D.risk || {}; D.liq = D.liq || {};
    showHealth(upd); showSurv(); showMonthly();
    opt('desc.json').then(d => { D.desc = d; return run() }).catch(e => console.log(e));
    run();
  } catch (e) { console.log(e); $('upd').textContent = '데이터 준비 중 (첫 자동 수집이 끝나면 표시됩니다)'; }
}
async function ensureGaps() { if (D.gaps) return; $('summary').textContent = '갭하락 자료 불러오는 중…'; D.gaps = await fetchJSON('gaps.json'); }
async function ensureOld(force) {
  if (oldLoaded) return;
  if (!force && $('shock').value >= '2024-03-05') return;
  $('summary').textContent = '예전 주가 불러오는 중…';
  const o = await fetchJSON('prices_old.json');
  for (const s in D.p) D.p[s] = (o.p[s] || o.dates.map(() => null)).concat(D.p[s]);
  D.dates = o.dates.concat(D.dates); oldLoaded = true;
}
// 날짜 → 그 날짜 이하 마지막 거래일 번호
function idxOn(date) { const ds = D.dates; let lo = 0, hi = ds.length - 1, r = -1; while (lo <= hi) { const m = (lo + hi) >> 1; if (ds[m] <= date) { r = m; lo = m + 1 } else hi = m - 1 } return r }
function priceAt(p, i) { for (let k = i; k >= 0 && k > i - 10; k--) if (p[k] != null) return p[k]; return null }

// ---------- 실적·가치 ----------
function quartersBefore(sym, date, lag = 0) { const f = D.fund[sym]; if (!f) return []; return f.q.filter(x => addDays(x[0], lag) < date) }
function yoy(qs, col) {
  if (!qs.length) return null; const cur = qs[qs.length - 1]; const t = new Date(cur[0]);
  const prev = qs.find(x => { const d = (t - new Date(x[0])) / 864e5; return d > 330 && d < 400 });
  if (!prev || cur[col] == null || prev[col] == null || prev[col] === 0) return null;
  return prev[col] > 0 ? pct(cur[col], prev[col]) : (cur[col] > prev[col] ? 999 : -999);
}
function fundCheck(sym, shock) {
  const f = D.fund[sym]; const out = { rev: null, op: null, ni: null, de: null, q: null };
  if (!f) return out;
  const qs = f.q.filter(x => x[0] < shock);
  if (qs.length) {
    out.q = qs[qs.length - 1][0]; out.rev = yoy(qs, 1); out.op = yoy(qs, 2);
    const last4 = qs.slice(-4);
    if (last4.length === 4 && last4.every(x => x[3] != null) && dayDiff(last4[0][0], last4[3][0]) < 300) out.ni = last4.reduce((a, x) => a + x[3], 0);
  }
  let b = null; for (const x of f.b) { if (x[0] < shock) b = x; else break }
  if (b && b[2] > 0) out.de = b[1] / b[2] * 100;
  return out;
}
// 악재 이후 발표된(분기말+45일이 기준일 이전) 실적
function postCheck(sym, shock, asOf) {
  const qs = quartersBefore(sym, asOf, 45); const last = qs[qs.length - 1];
  if (!last || last[0] < shock) return null;
  return { q: last[0], rev: yoy(qs, 1), op: yoy(qs, 2) };
}
function ttm(qs, col) { const l = qs.slice(-4); if (l.length < 4 || l.some(x => x[col] == null) || dayDiff(l[0][0], l[3][0]) > 300) return null; return l.reduce((a, x) => a + x[col], 0) }
function sharesAt(sym, date) { const f = D.fund[sym]; if (!f || !f.sh) return null; let r = null; for (const x of f.sh) if (x[0] <= date) r = x[1]; return r || (f.sh.length ? f.sh[f.sh.length - 1][1] : null) }
// PER(지금)·PER(최근 몇 년 중간값)·FCF 수익률
function valuation(sym, ai) {
  const p = D.p[sym]; const date = D.dates[ai]; const price = priceAt(p, ai); if (!price) return {};
  const per = i => { const d = D.dates[i]; const e = ttm(quartersBefore(sym, d, 45), 4); const pr = priceAt(p, i); return e > 0 && pr ? pr / e : (e != null && e <= 0 ? -1 : null) };
  const now0 = per(ai); const now = now0 > 0 && now0 < 3 ? null : now0; const hist = []; // PER 3 미만은 자료 오류로 보고 제외
  for (let i = ai - 21; i >= 0 && i > ai - 1260; i -= 21) { const v = per(i); if (v >= 3 && v < 300) hist.push(v) }
  const qs = quartersBefore(sym, date, 45); const ocf = ttm(qs, 5), cap = ttm(qs, 6); const sh = sharesAt(sym, date);
  let fcfy = ocf != null && sh ? (ocf - (cap || 0)) / (sh * price) * 100 : null;
  if (fcfy != null && Math.abs(fcfy) > 40) fcfy = null; // 비정상 값(주식 수 오류 등)은 표시 안 함
  return { per: now, perMed: hist.length >= 6 ? median(hist) : null, years: Math.round(hist.length * 21 / 252), fcfy, mcap: sh ? sh * price : null };
}
function estCheck(sym, shock) {
  const h = D.est[sym]; if (!h) return null;
  const ks = Object.keys(h).sort(); let base = null; for (const k of ks) { if (k < shock) base = k; else break }
  if (!base) return null; const a = h[base], b = h[ks[ks.length - 1]]; if (!(a > 0)) return null; return pct(b, a);
}
// 바닥 후 반등 신호: 20일 이동평균 위 + 최저가 이후 10거래일 이상
function bottomSignal(p, li, ai) {
  const w = []; for (let i = ai; i >= 0 && w.length < 20; i--) if (p[i] != null) w.push(p[i]);
  if (w.length < 20) return null; const ma = w.reduce((a, x) => a + x, 0) / 20; const now = w[0];
  return { ok: now > ma && ai - li >= 10, above: now > ma, days: ai - li };
}

// ---------- 그룹 지수 (중간값 수익률로 만든 지수) ----------
function members(g) { return Object.keys(D.meta).filter(s => g === '시장 전체' || D.meta[s].s === g || D.meta[s].i === g) }
function groupIndex(syms, from, to) {
  const out = [1];
  for (let i = from + 1; i <= to; i++) { const r = []; for (const s of syms) { const p = D.p[s]; if (p && p[i] != null && p[i - 1] != null) r.push(p[i] / p[i - 1] - 1) } out.push(out[out.length - 1] * (1 + (median(r) || 0))) }
  return out;
}

// ---------- 검색 (bt=true 이면 과거 검증용: 기준일 ai 시점의 정보만 사용) ----------
function screen(shock, ai, bt, shockDD) {
  const dates = D.dates; const preFrom = addDays(shock, -60), postTo = addDays(shock, 60);
  const a = idxOn(addDays(preFrom, -1)) + 1, i0 = idxOn(addDays(shock, -1)), b = Math.min(idxOn(postTo), ai);
  if (a < 0 || i0 < 0 || i0 >= ai) return null;
  const rows = [], secDrops = {};
  for (const s in D.meta) {
    const p = D.p[s]; if (!p) continue;
    if (bt && D.meta[s].a && D.meta[s].a > shock) continue; // 과거 검증: 그 당시 S&P 500에 없던 종목 제외
    let high = -1, hi = -1, low = Infinity, li = -1;
    for (let i = a; i <= i0; i++) if (p[i] != null && p[i] > high) { high = p[i]; hi = i }
    for (let i = i0 + 1; i <= b; i++) if (p[i] != null && p[i] < low) { low = p[i]; li = i }
    let now = priceAt(p, ai), liveQ = null;
    if (!bt && ai === D.dates.length - 1 && D.live && D.live.q && D.live.q[s]) { liveQ = D.live.q[s]; now = liveQ[0] } // 프리·장중·애프터마켓 현재가
    if (hi < 0 || li < 0 || now == null) continue;
    const r = { s, m: D.meta[s], pre: high, low, now, liveQ, close: priceAt(p, ai), hi, li, hd: dates[hi], ld: dates[li], dLow: pct(low, high), up: pct(high, now), rec: now / high * 100 };
    (secDrops[r.m.s] = secDrops[r.m.s] || []).push(r.dLow); rows.push(r);
  }
  const secMed = {}; for (const k in secDrops) secMed[k] = median(secDrops[k]);
  const minDrop = V('minDrop'), maxRec = V('maxRec'), minGap = V('minGap'), unkPass = C('unkPass');
  const asOf = dates[ai]; const res = []; let cand = 0, unkOut = 0;
  for (const r of rows) {
    if (r.dLow > -minDrop) continue;
    if (minGap > 0) { let g = 0; for (const [d, v] of (D.gaps[r.s] || [])) if (d >= shock && d <= postTo && v < g) { g = v; r.gd = d } r.gap = g; if (g > -minGap) continue }
    cand++;
    if (r.rec >= maxRec) continue;
    const f = fundCheck(r.s, shock), fin = r.m.s === '금융' || r.m.s === '부동산';
    const tests = [], extra = [];
    const T = (list, on, name, val, pass, txt, weak) => { if (!on) return; list.push({ name, st: val == null ? 'unk' : (!pass ? 'bad' : (weak ? 'warn' : 'ok')), txt: val == null ? name + ' 자료없음' : txt }) };
    // 기본 조건 (악재 전 기준)
    T(tests, C('cSec'), '업종', secMed[r.m.s], secMed[r.m.s] <= -V('vSec'), `업종 동반 ${secMed[r.m.s] != null ? fmt(secMed[r.m.s], 0) : ''}`);
    T(tests, true, '매출', f.rev, f.rev >= Math.max(10, V('vRev')), `매출 ${f.rev != null ? fmt(f.rev) : ''}`, f.rev <= 0);
    T(tests, C('cOp'), '영업이익', f.op, f.op >= V('vOp'), `영업이익 ${f.op == null ? '' : Math.abs(f.op) === 999 ? (f.op > 0 ? '적자축소' : '적자확대') : fmt(f.op)}`, f.op <= 0 || Math.abs(f.op) === 999);
    if (!bt) { const e = estCheck(r.s, shock); T(tests, C('cEst'), 'EPS전망', e, e >= -V('vEst'), `EPS전망 ${e != null ? fmt(e) : ''}`, e < 0) }
    if (C('cDe') && !fin) T(tests, true, '부채비율', f.de, f.de <= V('vDe'), `부채비율 ${f.de != null ? f.de.toFixed(0) + '%' : ''}`, f.de >= 100);
    T(tests, C('cNi'), '순이익', f.ni, f.ni > 0, f.ni > 0 ? '1년 흑자' : '1년 적자');
    if (r.now < r.low * 0.9) tests.push({ name: '추가하락', st: 'warn', txt: `2달 뒤에도 ${fmt(pct(r.now, r.low), 0)} 추가 하락 — 다른 원인 확인` });
    // 1. 악재 이후 실적
    const pc = postCheck(r.s, shock, asOf);
    if (pc && (pc.rev != null || pc.op != null)) {
      const down = (pc.rev != null && pc.rev < 0) || (pc.op != null && pc.op < 0);
      extra.push({ name: '악재후실적', st: down ? (C('cPost') ? 'bad' : 'warn') : 'ok', txt: `악재 후 실적(${pc.q.slice(0, 7)}) 매출 ${pc.rev != null ? fmt(pc.rev, 0) : '?'}·영업이익 ${pc.op == null ? '?' : Math.abs(pc.op) === 999 ? (pc.op > 0 ? '적자축소' : '적자확대') : fmt(pc.op, 0)}` });
    } else extra.push({ name: '악재후실적', st: 'info', txt: '악재 후 실적 아직 미발표' });
    // 2. 밸류에이션
    const v = valuation(r.s, ai); r.val = v;
    if (v.per === -1) extra.push({ name: 'PER', st: C('cVal') ? 'bad' : 'warn', txt: 'PER 적자' });
    else if (v.per) { const hiPer = v.perMed && v.per > v.perMed; extra.push({ name: 'PER', st: hiPer ? (C('cVal') ? 'bad' : 'warn') : 'ok', txt: `PER ${v.per.toFixed(1)}${v.perMed ? ` (최근 ${v.years}년 중간 ${v.perMed.toFixed(1)})` : ''}` }) }
    // 매도 적정가: 1차 = 낙폭의 절반 회복, 2차 = 악재 전 최고가, 참고 = 평소 PER × 최근 1년 EPS
    const eps = ttm(quartersBefore(r.s, asOf, 45), 4);
    r.tg = { t1: r.low + (r.pre - r.low) / 2, t2: r.pre, per: v.perMed && eps > 0 ? v.perMed * eps : null };
    if (v.fcfy != null) extra.push({ name: 'FCF', st: v.fcfy < 0 ? 'warn' : 'ok', txt: `잉여현금 수익률 ${v.fcfy.toFixed(1)}%` });
    // 3. 바닥 후 반등 신호
    const bs = bottomSignal(D.p[r.s], r.li, ai); r.bs = bs;
    if (bs) extra.push({ name: '반등', st: bs.ok ? 'ok' : (C('cBottom') ? 'bad' : 'warn'), txt: bs.ok ? `바닥 후 반등 중 (최저가 ${bs.days}거래일 전, 20일선 위)` : (bs.above ? `최저가 후 ${bs.days}거래일 — 반등 확인 전` : '20일선 아래 — 아직 하락 추세') });
    if (!bt) {
      // 5. 위험 정보 (현재 시점만)
      const rk = D.risk[r.s] || {};
      if (rk.ud) { const u = rk.ud.filter(x => x[0] >= shock); const up = u.filter(x => x[1] === 'up').length, dn = u.filter(x => x[1] === 'down').length; if (up || dn) extra.push({ name: '투자의견', st: dn > up ? (C('cDown') ? 'bad' : 'warn') : 'ok', txt: `악재 후 투자의견 상향 ${up}·하향 ${dn}` }) }
      if (rk.ed) { const dd = dayDiff(asOf, rk.ed); if (dd >= 0) extra.push({ name: '실적일', st: dd <= 14 ? (C('cEarn') ? 'bad' : 'warn') : 'info', txt: `실적발표 ${rk.ed.slice(5).replace('-', '/')} (D-${dd})` }) }
      if (rk.sp != null) extra.push({ name: '공매도', st: rk.sp >= 10 ? 'warn' : 'info', txt: `공매도 ${rk.sp}%` });
      if (rk.ins && rk.ins.net != null && rk.ins.net !== 0) extra.push({ name: '내부자', st: rk.ins.net > 0 ? 'ok' : 'info', txt: rk.ins.net > 0 ? '최근 6개월 내부자 순매수' : '최근 6개월 내부자 순매도' });
      const lq = D.liq[r.s]; if (lq != null) extra.push({ name: '거래대금', st: lq < 30 ? 'warn' : 'info', txt: `하루 거래대금 $${lq >= 1000 ? (lq / 1000).toFixed(1) + 'B' : Math.round(lq) + 'M'}` });
      if (v.mcap) extra.push({ name: '시총', st: 'info', txt: `시가총액 $${v.mcap >= 1e12 ? (v.mcap / 1e12).toFixed(2) + 'T' : (v.mcap / 1e9).toFixed(0) + 'B'}` });
    }
    // 회복 가능성 점수 (10년 분석 공통점 4가지)
    const p = D.p[r.s]; const px0 = priceAt(p, i0), px1 = i0 >= 252 ? priceAt(p, i0 - 252) : null;
    const mom = px0 && px1 ? pct(px0, px1) : null;
    const rr = []; for (let i = Math.max(1, i0 - 60); i <= i0; i++) if (p[i] != null && p[i - 1] != null) rr.push(p[i] / p[i - 1] - 1);
    const vol = rr.length >= 40 ? Math.sqrt(rr.reduce((a, x) => a + x * x, 0) / rr.length - (rr.reduce((a, x) => a + x, 0) / rr.length) ** 2) * Math.sqrt(252) * 100 : null;
    const sdd = shockDD != null ? Math.min(shockDD, secMed[r.m.s] ?? 0) : secMed[r.m.s];
    const sc = [
      { hit: mom != null && mom > 0, txt: mom == null ? '① 1년 추세 자료없음' : `① 악재 전 1년 ${fmt(mom, 0)}` },
      { hit: fin || (f.de != null && f.de <= 100), txt: fin ? '② 금융·부동산 (부채비율 제외)' : f.de == null ? '② 부채비율 자료없음' : `② 부채비율 ${f.de.toFixed(0)}%` },
      { hit: vol != null && vol <= 30, txt: vol == null ? '③ 변동성 자료없음' : `③ 변동성 연 ${vol.toFixed(0)}%` },
      { hit: sdd != null && sdd <= -20, txt: sdd == null ? '④ 악재 크기 자료없음' : `④ 악재 크기 ${fmt(sdd, 0)}` },
    ];
    r.score = sc.filter(x => x.hit).length; r.sc = sc;
    // 1년 내 회복 확률: 코로나 기간을 뺀 과거 자료에서 점수·회복 정도가 비슷했던 종목이 1년 안에 악재 전 최고가를 회복한 실제 비율
    if (D.model) { const bins = D.model.bins; let rb = bins.findIndex(b => r.rec < b); if (rb < 0 || rb > 3) rb = 3; const t = D.model.table[r.score][rb]; r.prob = t.p; r.probN = t.n; r.probH = t.h; r.probU = t.u; r.probLv = t.lv }
    if (r.score < V('vScore')) continue;
    const all = tests.concat(extra);
    if (all.some(t => t.st === 'bad')) continue;
    // EPS 전망은 이 사이트가 기록을 쌓기 시작한 뒤부터만 있으므로, 기록이 없다고 빼지는 않고 ? 로만 표시
    if (!unkPass && all.some(t => t.st === 'unk' && t.name !== 'EPS전망')) { unkOut++; continue }
    res.push({ ...r, drop: r.dLow, tests, extra, q: f.q, warn: all.filter(t => t.st === 'warn' || t.st === 'unk').length });
  }
  res.sort((x, y) => (y.prob ?? 0) - (x.prob ?? 0) || y.score - x.score || x.warn - y.warn || y.up - x.up);
  return { res, cand, secMed, i0, b, unkOut };
}

// ---------- 악재 뉴스·해소 지표 ----------
function findShock(shock) { return SHOCKS.find(e => Math.abs(new Date(e.d) - new Date(shock)) <= 5 * 864e5) }
function showNews(shock) {
  document.querySelectorAll('#presets button').forEach(b => b.classList.toggle('on', b.dataset.d === shock));
  const x = findShock(shock); const box = $('newsBox');
  if (!x) { box.style.display = 'none'; return }
  box.style.display = '';
  box.innerHTML = `<b>${esc(x.name)}</b> <small>· ${esc(x.who)} 최대 ${x.dd}%${x.guess ? ' · 원인은 뉴스 제목으로 자동 추정' : ''} · 그 무렵 해외 뉴스 (한국어 번역)</small>` +
    ((x.news || []).length ? x.news.map(n => `<a href="${esc(n.u)}" target="_blank" rel="noopener">${esc(n.k || n.t)}${n.k && n.k !== n.t ? `<br><small>${esc(n.t)}</small>` : ''} <small>· ${esc(n.s)} ${esc(n.d)}</small></a>`).join('') : '<div class="note" style="margin-top:5px">관련 뉴스를 찾지 못했습니다</div>');
}
function showResolve(shock, i0) {
  const box = $('resolve'); const ai = D.dates.length - 1; const b = Math.min(idxOn(addDays(shock, 60)), ai);
  const lines = [];
  const x = findShock(shock); const groups = (x && x.gk) || ['시장 전체'];
  for (const g of groups.slice(0, 2)) {
    const ms = members(g); if (ms.length < 3) continue;
    const from = Math.max(0, idxOn(addDays(shock, -60)));
    const idx = groupIndex(ms, from, ai); const off = i => i - from;
    let peak = -1, pk = 0; for (let i = from; i <= i0; i++) if (idx[off(i)] > peak) { peak = idx[off(i)]; pk = i }
    let low = Infinity, lw = 0; for (let i = i0 + 1; i <= b; i++) if (idx[off(i)] < low) { low = idx[off(i)]; lw = i }
    const now = idx[off(ai)]; const recov = (now - low) / (peak - low) * 100;
    const ma = idx.slice(-20).reduce((a, v) => a + v, 0) / 20;
    const name = g === '시장 전체' ? 'S&P 500 종목 중간값' : (D.meta[ms[0]].s === g ? g + ' 업종' : g);
    lines.push(`<div style="margin-top:6px">📊 <b>${esc(name)}</b>: 고점 대비 최대 ${fmt(pct(low, peak), 0)} → 지금 ${fmt(pct(now, peak), 0)} · <b>${isFinite(recov) ? Math.max(0, Math.round(recov)) : '-'}% 회복</b> · ${now > ma ? '20일선 위(반등 흐름)' : '20일선 아래(약세)'}</div>`);
  }
  if (D.macro) {
    const md = D.macro.dates; const mi = d => { let r = -1; for (let i = 0; i < md.length; i++) { if (md[i] <= d) r = i; else break } return r };
    const m0 = mi(addDays(shock, -1)), m1 = mi(addDays(shock, 60)), mn = md.length - 1;
    for (const [name, s] of Object.entries(D.macro.s)) {
      const pre = s[m0]; if (pre == null) continue; let ext = pre;
      for (let i = m0 + 1; i <= m1; i++) if (s[i] != null && Math.abs(s[i] - pre) > Math.abs(ext - pre)) ext = s[i];
      let now = null; for (let i = mn; i > 0; i--) if (s[i] != null) { now = s[i]; break }
      const move = pct(ext, pre); if (Math.abs(move) < 3) continue; // 악재 때 크게 안 움직인 지표는 생략
      const res = ext !== pre ? (ext - now) / (ext - pre) * 100 : 0;
      const u = name.includes('금리') ? '%' : '';
      lines.push(`<div style="margin-top:4px">• ${name}: 악재 전 ${pre}${u} → 악재 중 ${ext}${u} (${fmt(move, 0)}) → 지금 ${now}${u} · <b>${Math.round(res)}% 되돌림</b></div>`);
    }
  }
  if (!lines.length) { box.style.display = 'none'; return }
  box.style.display = '';
  box.innerHTML = `<b>악재 해소 지표</b> <small>· 100%에 가까울수록 악재 전 수준으로 돌아온 것</small>${lines.join('')}`;
}

// ---------- 화면 ----------
async function run() {
  if (!D.meta) return;
  const shock = $('shock').value; if (!shock) return;
  await ensureOld(false);
  if (V('minGap') > 0) await ensureGaps();
  showNews(shock);
  const ai = D.dates.length - 1;
  const fx = findShock(shock); const out = screen(shock, ai, false, fx ? fx.dd : null);
  if (!out) { $('summary').textContent = '해당 날짜의 주가 자료가 없습니다.'; $('list').innerHTML = ''; $('resolve').style.display = 'none'; return }
  const { res, cand, secMed, i0, unkOut } = out;
  showResolve(shock, i0);
  $('secBox').style.display = '';
  $('secs').innerHTML = Object.entries(secMed).sort((a, b) => a[1] - b[1]).map(([k, v]) => `<span class="b">${k} ${fmt(v, 0)}</span>`).join('');
  const minGap = V('minGap');
  $('summary').innerHTML = `<b>${shock}</b> 악재 · 전후 2달 ${V('minDrop')}% 이상 하락${minGap > 0 ? ` + 갭하락 ${minGap}%↑` : ''} <b>${cand}</b>종목 중 아직 회복 전 + 조건 통과${V('vScore') > 0 ? ` + 회복 가능성 ${V('vScore')}점 이상` : ''} <b>${res.length}</b>종목 <span class="note">(1년 내 회복 확률 높은 순)</span>${unkOut ? `<br><span class="note">자료가 없어 확인 못 한 종목 <b>${unkOut}</b>개는 뺐습니다 — 보려면 '가치 유지 조건 설정' 맨 아래 <b>자료가 없는 조건은 통과로 처리</b>를 켜세요.</span>` : ''}`;
  const mix = {}; res.forEach(r => mix[r.m.s] = (mix[r.m.s] || 0) + 1);
  $('mix').innerHTML = res.length ? `결과 업종 분포: ${Object.entries(mix).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${k} ${v}`).join(' · ')} — 한 업종·한 종목에 몰리지 않게 나눠 담는 것이 일반적인 위험 관리 방법입니다.` : '';
  const badge = t => `<span class="b ${t.st}">${t.st === 'ok' ? '✓' : t.st === 'bad' ? '✗' : t.st === 'warn' ? '△' : t.st === 'info' ? '·' : '?'} ${t.txt}</span>`;
  $('list').innerHTML = res.length ? res.map(r => {
    const rk = D.risk[r.s] || {}; const yq = r.s.replace('.', '-');
    const news = (rk.news || []).map(n => `<a href="${esc(n.u)}" target="_blank" rel="noopener">${esc(n.k || n.t)} <small style="color:var(--mute)">· ${esc(n.s)} ${esc(n.d)}</small></a>`).join('');
    return `<div class="card"><div class="top"><div><span class="tk"><a href="https://finance.yahoo.com/quote/${yq}" target="_blank">${r.s}</a></span>
   <span class="nm"> ${esc(r.m.n)}</span></div><div style="text-align:right">${r.prob != null ? `<div class="prob">회복 확률 ${Math.round(r.prob * 100)}%</div>` : ''}<div class="up">회복 시 ${fmt(r.up, 0)}</div><div class="drop">낙폭 ${fmt(r.drop)}</div></div></div>
   ${D.desc && D.desc[r.s] ? `<div class="desc">${esc(D.desc[r.s])}</div>` : ''}
   <div class="sell">💰 매도 적정가 · 1차 <b>$${r.tg.t1.toFixed(2)}</b> <small>(${fmt(pct(r.tg.t1, r.now), 0)}, 낙폭 절반 회복)</small> · 2차 <b>$${r.tg.t2.toFixed(2)}</b> <small>(${fmt(pct(r.tg.t2, r.now), 0)}, 악재 전 최고가)</small>${r.tg.per && r.tg.per > r.now ? ` · 참고 <b>$${r.tg.per.toFixed(2)}</b> <small>(평소 PER 기준)</small>` : ''}</div>
   <div class="px">${r.m.s} · ${esc(r.m.i)} · ${D.div && D.div[r.s] ? `연 배당률 <b>${(D.div[r.s] / r.now * 100).toFixed(2)}%</b> <small>(최근 1년 주당 $${D.div[r.s].toFixed(2)})</small>` : '배당 없음'}<br>현재가 <b>$${r.now.toFixed(2)}</b> ${r.liveQ ? `<small>(${r.liveQ[2]} ${r.liveQ[1]} 미국 동부시간, 약 15분 지연 · 최근 일봉 종가 $${r.close.toFixed(2)})</small>` : `<small>(${D.dates[D.dates.length - 1].slice(5)} 종가)</small>`}<br>악재 전 최고 $${r.pre.toFixed(2)} (${r.hd.slice(5)}) → 최저 $${r.low.toFixed(2)} (${r.ld.slice(5)})${r.q ? ` · 실적 기준 분기 ${r.q.slice(0, 7)}` : ''}${r.gap ? ` · 최대 갭하락 ${r.gap}% (${r.gd.slice(5)})` : ''}</div>
   <div class="badges"><span class="b ${r.score >= 3 ? 'ok' : r.score === 2 ? 'warn' : 'bad'}" style="font-weight:700">회복 가능성 ${r.score}/4</span>${r.prob != null ? `<span class="b info">코로나 제외 과거 ${r.probLv === 'cell' ? '같은 점수·비슷한 회복 정도' : '같은 점수'} ${r.probN}건 중 ${r.probH}건 1년 내 최고가 회복 · 1년 뒤 상승 ${Math.round(r.probU * 100)}%${r.probN < 30 ? ' (표본 적음)' : ''}</span>` : ''}${r.sc.map(x => `<span class="b ${x.hit ? 'ok' : 'info'}">${x.hit ? '✓' : '·'} ${x.txt}</span>`).join('')}${peakTxt(r)}</div>
   <div class="badges sec2">${r.tests.map(badge).join('')}</div>
   <div class="badges sec2">${r.extra.map(badge).join('')}</div>
   <details class="more"><summary>최근 회사 뉴스</summary>${news || '<div class="note">저장된 뉴스 없음</div>'}
   <a href="https://news.google.com/search?q=${encodeURIComponent(r.m.n + ' stock')}&hl=ko&gl=KR&ceid=KR:ko" target="_blank" rel="noopener">구글 뉴스에서 더 보기 →</a></details></div>`
  }).join('') : '<div class="empty">조건을 모두 통과한 종목이 없습니다.<br>하락률이나 조건을 조금 낮춰 보세요.</div>';
}

// 예상 고점 시기: 과거 비슷한 점수 종목이 악재 저점 뒤 1년 안 최고가에 닿기까지 걸린 거래일(25~75%)
function peakTxt(r) {
  const pk = D.model && D.model.peak && D.model.peak[r.score]; if (!pk || !r.ld) return '';
  const td = n => addDays(r.ld, Math.round(n * 365 / 252)).slice(2).replace(/-/g, '.');
  const mo = D.model.monthly && D.model.monthly[r.score], pc = v => v == null ? '-' : Math.round(v * 100) + '%';
  const mtxt = mo && mo[0] && mo[0].n >= 10 ? `<span class="b info" title="같은 점수(${r.score}점) 과거 ${mo[11].n}건, 신호일(악재 2달 뒤) 기준 누적 회복 확률">최고가 회복 확률 3개월 ${pc(mo[2].rec)} · 6개월 ${pc(mo[5].rec)} · 12개월 ${pc(mo[11].rec)}</span>` : '';
  return `<span class="b info" title="추정일 뿐입니다. 표본 ${pk.n}건${pk.lv === 'all' ? '(같은 점수 표본이 적어 전체 사용)' : ''}">예상 고점 시기(과거 ${pk.n}건 추정) ${td(pk.p25)}~${td(pk.p75)} · 가운데 ${td(pk.p50)}</span>${mtxt}`;
}
// 월별 확률 표 (신호일 = 악재 2달 뒤 진입일, 코로나 제외, 그 당시 구성종목 기준)
function showMonthly() {
  const box = $('monTbl'); if (!box) return; const M = D.model && D.model.monthly;
  if (!M) { box.innerHTML = '<p class="note">월별 확률은 다음 10년 백테스트 계산 뒤에 표시됩니다.</p>'; return }
  const pc = v => v == null ? '-' : Math.round(v * 100) + '%', f1 = v => v == null ? '-' : (v > 0 ? '+' : '') + v.toFixed(0) + '%';
  const g = (o) => `<td>${pc(o.rec)}</td><td>${pc(o.up)}</td><td>${f1(o.avg)}<br><small>${f1(o.med)}</small></td>`;
  box.innerHTML = `<div style="overflow-x:auto"><table class="bt mon"><tr><th rowspan="2">신호 후</th><th colspan="3">기본 조건 (3점 이상, ${M.s3[11].n}건)</th><th colspan="3">조건 통과 전체 (${M.all[11].n}건)</th></tr><tr><th>최고가 회복</th><th>신호일보다 ↑</th><th>평균<br><small>중앙</small></th><th>최고가 회복</th><th>신호일보다 ↑</th><th>평균<br><small>중앙</small></th></tr>` +
    M.all.map((o, i) => `<tr${[2, 5, 11].includes(i) ? ' style="font-weight:600"' : ''}><td>${o.k}개월</td>${g(M.s3[i])}${g(o)}</tr>`).join('') + '</table></div>';
}

// ---------- 자료 상태 경고 · 생존편향 안내 ----------
const STEP_KO = { prices: '주가(일봉)', live: '현재가', shocks: '악재 찾기', fund: '실적(SEC)', est: 'EPS 전망', risk: '회사 뉴스·위험', risk40: '관심종목 뉴스', desc: '회사 설명', translate: '번역(실패한 곳은 영어 원문 표시)' };
function showHealth(upd) {
  const box = $('health'); if (!box) return;
  const msgs = (upd.fails || []).map(k => `${STEP_KO[k] || k} 갱신 실패${upd.st && upd.st[k] ? ` (${new Date(upd.st[k][1]).toLocaleString('ko-KR', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' })}부터)` : ''}`);
  const age = (Date.now() - new Date(upd.t)) / 36e5;
  if (age > 72) msgs.push(`자료가 ${Math.floor(age / 24)}일째 바뀌지 않았습니다 (주말·휴장이 아니라면 자동 갱신을 확인하세요)`);
  box.style.display = msgs.length ? '' : 'none';
  box.innerHTML = msgs.length ? '⚠️ ' + msgs.map(esc).join(' · ') + ' — 마지막으로 받은 자료를 보여 주고 있습니다.' : '';
}
function showSurv() {
  const el = $('survNote'); if (!el) return; const sv = D.model && D.model.surv;
  el.innerHTML = sv && sv.pit
    ? `확률은 <b>그 당시 S&P 500 구성종목</b> 기준입니다(나중에 빠진 종목 포함). 과거 구성종목 ${sv.members}개 중 지금은 빠진 ${sv.removed}개가 있고, 그중 <b>${sv.no_price}개는 상장폐지 등으로 주가 자료가 없어</b> 계산에서 빠졌습니다. 이 때문에 확률이 실제보다 조금 높게 나올 수 있습니다.`
    : '⚠️ 확률은 <b>지금 S&P 500에 있는 종목만</b>으로 계산한 값입니다. 그동안 지수에서 빠지거나 상장폐지된 회사가 빠져 있어 실제보다 높게 나올 수 있습니다(구성종목 기록으로 다시 계산 중).';
}

// ---------- 4. 과거 성과 검증 ----------
async function backtest() {
  const box = $('bt'); box.innerHTML = '<p class="note">계산 중… (처음에는 예전 주가를 불러오느라 몇 초 걸립니다)</p>';
  await ensureOld(true); if (V('minGap') > 0) await ensureGaps();
  const hs = await fetchJSON('hist_shocks.json').catch(() => []);
  if (!hs.length) { box.innerHTML = '<p class="note">과거 악재 목록이 아직 없습니다. 다음 자동 갱신 후 다시 눌러 주세요.</p>'; return }
  const last = D.dates.length - 1; const spx = D.macro && D.macro.s['S&P 500'];
  const spAt = d => { if (!spx) return null; const md = D.macro.dates; let r = -1; for (let i = 0; i < md.length; i++) { if (md[i] <= d) r = i; else break } return r >= 0 ? spx[r] : null };
  const H = [63, 126, 252]; const rows = []; const all = H.map(() => []); const ex = H.map(() => []);
  for (const h of hs) {
    const entry = idxOn(addDays(h.d, 61)); if (entry < 0 || entry > last - 5) continue;
    const out = screen(h.d, entry, true, h.dd); if (!out) continue;
    const row = { h, n: out.res.length, r: [], sp: [] };
    H.forEach((k, j) => {
      const t = entry + k; if (t > last) { row.r.push(null); row.sp.push(null); return }
      const rets = out.res.map(c => { const p0 = priceAt(D.p[c.s], entry), p1 = priceAt(D.p[c.s], t); return p0 && p1 ? pct(p1, p0) : null }).filter(x => x != null);
      const avg = rets.length ? rets.reduce((a, x) => a + x, 0) / rets.length : null;
      const s0 = spAt(D.dates[entry]), s1 = spAt(D.dates[t]); const sp = s0 && s1 ? pct(s1, s0) : null;
      row.r.push(avg); row.sp.push(sp);
      rets.forEach(x => { all[j].push(x); if (sp != null) ex[j].push(x - sp) });
    });
    rows.push(row);
  }
  const cell = v => v == null ? '<td class="note">-</td>' : `<td class="${v >= 0 ? 'pos' : 'neg'}">${fmt(v, 0)}</td>`;
  const avg = a => a.length ? a.reduce((s, x) => s + x, 0) / a.length : null;
  const win = a => a.length ? Math.round(a.filter(x => x > 0).length / a.length * 100) : null;
  box.innerHTML = `<table class="bt"><tr><th>악재 (진입: 2달 뒤)</th><th>종목</th><th>3개월</th><th>6개월</th><th>1년</th><th>S&P 1년</th></tr>` +
    rows.map(r => `<tr><td>${r.h.d.slice(0, 7)} ${esc(r.h.name)}</td><td>${r.n}</td>${r.r.map(cell).join('')}${cell(r.sp[2])}</tr>`).join('') +
    `<tr style="font-weight:600"><td>악재별 평균의 평균</td><td>${rows.filter(r => r.n).length}회</td>${[0, 1, 2].map(j => cell(avg(rows.filter(r => r.n && r.r[j] != null).map(r => r.r[j])))).join('')}${cell(avg(rows.filter(r => r.n && r.sp[2] != null).map(r => r.sp[2])))}</tr>` +
    `<tr style="font-weight:600"><td>전체 평균 (종목별)</td><td>${all[0].length}</td>${all.map(a => cell(avg(a))).join('')}<td></td></tr></table>` +
    `<p class="note" style="margin-top:8px"><b>S&P 500을 이긴 악재: ${rows.filter(r => r.r[2] != null && r.sp[2] != null).filter(r => r.r[2] > r.sp[2]).length} / ${rows.filter(r => r.r[2] != null && r.sp[2] != null).length}회 (1년 기준)</b> · 악재별 1년 초과수익 중간값 ${(() => { const m = median(rows.filter(r => r.r[2] != null && r.sp[2] != null).map(r => r.r[2] - r.sp[2])); return m == null ? '-' : fmt(m, 1) })()}<br>수익 낸 종목 비율: 3개월 ${win(all[0]) ?? '-'}% · 6개월 ${win(all[1]) ?? '-'}% · 1년 ${win(all[2]) ?? '-'}%<br>
     같은 기간 S&P 500보다 평균 ${ex[2].length ? fmt(avg(ex[2]), 1) : '-'} (1년 기준) · 종목 수가 적은 행은 우연의 영향이 큽니다.</p>`;
}

$('go').onclick = run;
$('vScore').onchange = run;
$('btGo').onclick = backtest;
init();
// 페이지를 열어 둔 동안에도 10분마다 새 데이터 확인
setInterval(async () => { try { const u = await fetchJSON('updated.json'); if (u.t !== D.updT) { oldLoaded = false; D.gaps = null; D.desc = null; await init() } } catch (e) { } }, 600000);
