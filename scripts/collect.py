"""S&P 500 악재 낙폭 검색기 - 데이터 수집 (GitHub Actions에서 매일 실행)

만드는 파일 (docs/data/):
  meta.json          종목명·업종
  prices_recent.json 2024-01-01 이후 일별 수정종가
  prices_old.json    2019-06-01 ~ 2023-12-31 일별 수정종가 (예전 악재 날짜용)
  fund.json          SEC 공시 기반 분기 매출·영업이익·순이익, 부채·자본
  est.json           애널리스트 내년 EPS 추정치 기록 (매일 쌓임)
  updated.json       갱신 시각
"""
import csv
import io
import json
import os
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone

DATA = os.path.join(os.path.dirname(__file__), "..", "docs", "data")
os.makedirs(DATA, exist_ok=True)
UA = "sp500-dip research gohjunwon@gmail.com"
CONSTITUENTS = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

SECTOR_KO = {
    "Information Technology": "정보기술", "Health Care": "헬스케어", "Financials": "금융",
    "Consumer Discretionary": "경기소비재", "Communication Services": "커뮤니케이션",
    "Industrials": "산업재", "Consumer Staples": "필수소비재", "Energy": "에너지",
    "Utilities": "유틸리티", "Real Estate": "부동산", "Materials": "소재",
}

REV_TAGS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
    "RevenuesNetOfInterestExpense", "InterestAndDividendIncomeOperating",
]
OP_TAGS = [
    "OperatingIncomeLoss",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
]
NI_TAGS = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read()
        except Exception as e:  # noqa
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def save(name, obj):
    with open(os.path.join(DATA, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def load(name, default):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------- 1. 종목 목록 ----------
def constituents():
    rows = list(csv.DictReader(io.StringIO(get(CONSTITUENTS).decode("utf-8"))))
    out = {}
    for r in rows:
        out[r["Symbol"].strip()] = {
            "n": r["Security"].strip(),
            "s": SECTOR_KO.get(r["GICS Sector"].strip(), r["GICS Sector"].strip()),
            "i": r["GICS Sub-Industry"].strip(),
            "cik": int(r["CIK"]) if r.get("CIK", "").strip().isdigit() else None,
            "a": (r.get("Date added") or "").strip()[:10],
        }
    return out


# ---------- 2. 주가 ----------
def prices(symbols):
    import yfinance as yf
    ymap = {s: s.replace(".", "-") for s in symbols}
    raw = yf.download(list(ymap.values()), start="2019-06-01", auto_adjust=True,
                      progress=False, threads=True)
    df = raw["Close"].dropna(how="all")
    op = raw["Open"].reindex(df.index)
    # 갭하락: 시가가 전날 종가보다 1% 이상 낮게 시작한 날만 기록 (용량 절약)
    gap = (op / df.shift(1) - 1) * 100
    gaps = {}
    for s, y in ymap.items():
        if y not in gap.columns:
            continue
        col = gap[y]
        hit = col[col <= -1].dropna()
        if len(hit):
            gaps[s] = [[d.strftime("%Y-%m-%d"), round(float(v), 1)] for d, v in hit.items()]
    save("gaps.json", gaps)
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    cut = next(i for i, d in enumerate(dates) if d >= "2024-01-01")
    recent = {"dates": dates[cut:], "p": {}}
    old = {"dates": dates[:cut], "p": {}}
    for s, y in ymap.items():
        if y not in df.columns:
            continue
        col = [None if v != v else round(float(v), 2) for v in df[y].tolist()]
        if all(v is None for v in col[cut:]):
            continue
        recent["p"][s] = col[cut:]
        old["p"][s] = col[:cut]
    save("prices_recent.json", recent)
    save("prices_old.json", old)
    # 최근 20일 평균 거래대금(달러)
    try:
        vol = raw["Volume"].reindex(df.index)
        dv = (vol * df).tail(20).mean()
        save("liq.json", {s: round(float(dv[y]) / 1e6, 1) for s, y in ymap.items()
                          if y in dv.index and dv[y] == dv[y]})
    except Exception as e:
        print("liq fail", e)
    macro(df.index)
    print(f"prices: {len(recent['p'])} tickers, {len(dates)} days")
    return df.rename(columns={y: s for s, y in ymap.items()})


# ---------- 2-00. 실시간에 가까운 현재가 (프리마켓·장중·애프터마켓 포함, 약 15분 지연) ----------
def live_quotes(symbols):
    import yfinance as yf
    from zoneinfo import ZoneInfo
    ymap = {s: s.replace(".", "-") for s in symbols}
    raw = yf.download(list(ymap.values()), period="2d", interval="5m", prepost=True,
                      progress=False, threads=True)["Close"]
    et = ZoneInfo("America/New_York")
    q = {}
    for s, y in ymap.items():
        if y not in raw.columns:
            continue
        col = raw[y].dropna()
        if col.empty:
            continue
        ts = col.index[-1].tz_convert(et)
        hm = ts.hour * 60 + ts.minute
        sess = "프리마켓" if hm < 570 else "장중" if hm < 960 else "애프터마켓"
        q[s] = [round(float(col.iloc[-1]), 2), ts.strftime("%m/%d %H:%M"), sess]
    save("live.json", {"t": datetime.now(timezone.utc).isoformat(timespec="minutes"), "q": q})
    print(f"live: {len(q)}")


# ---------- 2-0. 악재 해소 지표 (S&P500·VIX·유가·미국 10년 금리·달러) ----------
MACRO = {"^GSPC": "S&P 500", "^VIX": "VIX 공포지수", "CL=F": "WTI 유가", "^TNX": "미국 10년 금리", "DX-Y.NYB": "달러 지수"}


def macro(index):
    import yfinance as yf
    raw = yf.download(list(MACRO), start="2019-06-01", progress=False, auto_adjust=True)["Close"]
    raw = raw.reindex(index).ffill()
    out = {"dates": [d.strftime("%Y-%m-%d") for d in index], "s": {}}
    for k, name in MACRO.items():
        if k in raw.columns:
            out["s"][name] = [None if v != v else round(float(v), 2) for v in raw[k].tolist()]
    save("macro.json", out)


# ---------- 2-1. 악재 자동 찾기 ----------
KNOWN = [  # 이름 붙이기용 (날짜 -7일~+14일 안이면 이 이름 사용), 뉴스 검색어
    ("2020-02-21", "코로나19 팬데믹", "coronavirus stocks plunge"),
    ("2022-01-04", "금리 인상 충격", "Fed rate hike stocks fall"),
    ("2022-05-05", "금리 인상·인플레이션", "inflation stocks sell-off"),
    ("2023-03-09", "SVB 은행 파산", "SVB bank stocks plunge"),
    ("2024-08-01", "엔캐리 청산", "yen carry trade stocks plunge"),
    ("2025-04-03", "트럼프 상호관세", "tariffs stocks plunge"),
    ("2026-03-02", "이란 전쟁·호르무즈 해협 봉쇄", "Strait of Hormuz stocks fall"),
]


NEWS_TERM = {
    "시장 전체": "뉴욕증시", "정보기술": "기술주", "헬스케어": "헬스케어주", "금융": "은행주",
    "경기소비재": "소비재주", "커뮤니케이션": "통신주", "산업재": "산업재", "필수소비재": "필수소비재",
    "에너지": "에너지주", "유틸리티": "유틸리티주", "부동산": "리츠", "소재": "소재주",
}


NEWS_KEYS = ["slump", "tumble", "sink", "drop", "rout", "crash", "selloff", "tariff", "rate", "fear", "증시", "주가", "급락", "하락", "폭락", "나스닥", "s&p", "다우", "주식", "기술주", "은행주", "매도",
             "관세", "금리", "stock", "market", "plunge", "fall", "sell-off", "selloff", "nasdaq", "dow"]


def google_news(q, d_from, d_to, lang="ko"):
    import urllib.parse
    import xml.etree.ElementTree as ET
    loc = "hl=ko&gl=KR&ceid=KR:ko" if lang == "ko" else "hl=en-US&gl=US&ceid=US:en"
    url = ("https://news.google.com/rss/search?q=" +
           urllib.parse.quote(f"{q} after:{d_from} before:{d_to}") + "&" + loc)
    try:
        root = ET.fromstring(get(url))
    except Exception as e:
        print("news fail", q, e)
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        src = (it.findtext("source") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        items.append({"t": title, "u": it.findtext("link") or "", "s": src,
                      "d": (it.findtext("pubDate") or "")[5:16]})
    return items


SECTOR_EN = {v: k for k, v in SECTOR_KO.items()}


def _gtx(text):
    import urllib.parse
    url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=ko&dt=t&q="
           + urllib.parse.quote(text))
    data = json.loads(get(url, tries=4))
    return "".join(seg[0] for seg in data[0] if seg and seg[0])


def _mymemory(text):
    import urllib.parse
    url = ("https://api.mymemory.translated.net/get?langpair=en|ko&de=gohjunwon@gmail.com&q="
           + urllib.parse.quote(text[:450]))
    return json.loads(get(url)).get("responseData", {}).get("translatedText", "") or ""


def translate_many(texts):
    """영어 여러 줄 → 한국어 (한 번에 묶어 요청, 실패하면 다른 번역 사이트로)"""
    texts = [t.replace("\n", " ") for t in texts]
    if not texts:
        return []
    try:
        out = _gtx("\n".join(texts)).split("\n")
        if len(out) == len(texts):
            return [o.strip() for o in out]
    except Exception as e:
        print("translate(google) fail", e)
    res = []
    for t in texts:
        try:
            res.append(_mymemory(t).strip())
        except Exception as e:
            print("translate(mymemory) fail", e)
            res.append("")
        time.sleep(1)
    return res


def translate_ko(text):
    return (translate_many([text]) or [""])[0]


def shock_news(e, n=5):
    """악재 날짜 앞뒤 영어 뉴스 제목을 받아 한국어 번역과 함께 붙이기"""
    d = date.fromisoformat(e["d"])
    d_from, d_to = (d - timedelta(days=3)).isoformat(), (d + timedelta(days=7)).isoformat()
    first = e["who"].split("·")[0].replace(" 등", "")
    en = e.get("en", "Stock market")
    if en == "Stock market":
        queries = ["stock market sell-off", "stocks plunge Wall Street", "S&P 500 falls why"]
    else:
        queries = [f"{en} stocks plunge", f"{en} stocks sell-off", "stock market sell-off"]
    if e.get("q"):
        queries = [e["q"]] + queries
    out, seen = [], set()
    for q in queries:
        for it in google_news(q, d_from, d_to, "en"):
            key = it["t"][:30].lower()
            if key in seen or not any(k in it["t"].lower() for k in NEWS_KEYS):
                continue
            seen.add(key)
            out.append(it)
            if len(out) >= n:
                break
        if len(out) >= n:
            break
        time.sleep(1)
    if out:
        for it, k in zip(out, translate_many([it["t"] for it in out])):
            it["k"] = k
        return out
    if not out:  # 영어 뉴스가 없으면 한국어 뉴스로
        for it in google_news(f"{NEWS_TERM.get(first, '뉴욕증시')} 급락", d_from, d_to, "ko"):
            if any(k in it["t"].lower() for k in NEWS_KEYS):
                it["k"] = it["t"]
                out.append(it)
                if len(out) >= n:
                    break
    return out


def _detect(df, meta, rets, groups, m_thr, s_thr, i_thr, since):
    import pandas as pd
    events = []
    for g, cols in groups.items():
        idx = (1 + rets[cols].median(axis=1).fillna(0)).cumprod()
        thr = m_thr if g == "시장 전체" else (s_thr if g in SECTOR_EN else i_thr)
        dates = idx.index
        dd = idx / idx.rolling("61D").max() - 1
        i = int((dates < pd.Timestamp(since)).sum())
        while i < len(idx):
            if dd.iloc[i] <= -thr:
                t = dates[i]
                win = idx[(dates >= t - pd.Timedelta(days=60)) & (dates <= t)]
                peak_d = win.idxmax()
                seg = idx[(dates > peak_d) & (dates <= peak_d + pd.Timedelta(days=60))]
                trough_d = seg.idxmin()
                # 급락이 실제로 시작된 날: 고점의 99% 위에 있던 마지막 날의 다음 거래일
                mid = idx[(dates >= peak_d) & (dates < trough_d)]
                start_d = mid[mid >= idx[peak_d] * 0.99].index[-1]
                shock = idx[dates > start_d].index[0]
                if shock >= pd.Timestamp(since):
                    events.append({"d": shock.strftime("%Y-%m-%d"), "g": g, "t": trough_d.strftime("%Y-%m-%d"),
                                   "dd": round(float(seg.min() / idx[peak_d] - 1) * 100, 1)})
                i = max(i + 1, int((dates <= trough_d + pd.Timedelta(days=20)).sum()))
                continue
            i += 1
    events.sort(key=lambda e: e["d"])
    merged = []
    for e in events:  # 시작일이 10일 안으로 겹치는 급락은 하나로
        if merged and _days(merged[-1]["d"], e["d"]) <= 10:
            m = merged[-1]
            if e["g"] not in m["g"]:
                m["g"].append(e["g"])
            m["dd"] = min(m["dd"], e["dd"])
            m["t"] = max(m["t"], e["t"])
        else:
            merged.append({"d": e["d"], "g": [e["g"]], "dd": e["dd"], "t": e["t"]})
    return merged


# 시장 전체는 2달 안에 5% 이상 빠지면 항상 포함, 나머지는 업종·세부업종 급락을 낙폭 큰 순으로 채움
MARKET_THR = 0.05
# 업종 %, 세부업종 % — 6개가 될 때까지 조금씩 낮춤
TIERS = [(0.15, 0.20), (0.12, 0.17), (0.10, 0.15), (0.08, 0.12)]


def hist_shocks(df, meta, rets, market, groups, names):
    """과거 성과 검증용: 2019년 9월 이후 큰 악재 전체 (시장 8%↓, 업종 15%↓, 세부업종 25%↓)"""
    mk = _detect(df, meta, rets, market, 0.08, 1, 1, "2019-09-01")
    others = _detect(df, meta, rets, groups, 1, 0.15, 0.25, "2019-09-01")
    for o in others:
        m = next((m for m in mk if abs(_days(m["d"], o["d"])) <= 10), None)
        if m:
            m["g"] += [g for g in o["g"] if g not in m["g"]]
        else:
            mk.append(o)
    level = lambda g: 0 if g == "시장 전체" else 1 if g in SECTOR_EN else 2
    need = sorted({g for m in mk for g in m["g"] if level(g) == 2 and names.get(g, g) == g})
    for i in range(0, len(need), 12):  # 세부업종 이름 한국어로
        for g, k in zip(need[i:i + 12], translate_many(need[i:i + 12])):
            if k:
                names[g] = k
    save("ko_names.json", names)
    res = []
    for m in sorted(mk, key=lambda m: m["d"]):
        gs = sorted(m["g"], key=level)
        kn = next((k for k in KNOWN if -7 <= _days(m["d"], k[0]) <= 14), None)
        who = "시장 전체" if gs[0] == "시장 전체" else "·".join(names.get(g, g) for g in gs[:2])
        res.append({"d": m["d"], "name": kn[1] if kn else f"{who} 급락", "dd": m["dd"], "gk": gs[:3]})
    save("hist_shocks.json", res)
    print("hist shocks:", len(res))


# 뉴스 제목에 많이 나온 단어로 악재 원인 추정 (영어 → 한국어)
CAUSES = [
    (("tariff", "trade war", "trade deal", "trade tension"), "관세·무역분쟁"),
    (("iran", "hormuz", "israel", "missile", "war ", "invasion", "ukraine", "russia"), "전쟁·지정학"),
    (("oil", "crude", "opec"), "유가"),
    (("fed ", "fed's", "federal reserve", "powell", "rate hike", "interest rate"), "금리·연준"),
    (("inflation", "cpi"), "인플레이션"),
    (("bond", "yield", "treasur"), "채권금리"),
    ((" ai ", "ai-", "ai spending", "artificial intelligence"), "AI 우려"),
    (("chip", "semiconductor", "nvidia"), "반도체"),
    (("export control", "export curb", "export ban", "china"), "중국·수출규제"),
    (("bank", "lender", "credit"), "은행·신용"),
    (("virus", "covid", "pandemic", "outbreak"), "전염병"),
    (("recession", "slowdown", "jobs report", "unemployment"), "경기침체 우려"),
    (("medicare", "medicaid", "drug pric", "health insur"), "의료 정책"),
    (("earnings", "guidance", "forecast cut"), "실적 실망"),
    (("shutdown", "debt ceiling", "election"), "정치"),
    (("crypto", "bitcoin"), "암호화폐"),
]


def guess_cause(news):
    text = " " + " ".join(n.get("t", "") for n in news).lower() + " "
    score = []
    for keys, label in CAUSES:
        c = sum(text.count(k) for k in keys)
        if c:
            score.append((c, label))
    score.sort(reverse=True)
    return [l for c, l in score[:2] if c >= 2] or [l for c, l in score[:1]]


def find_shocks(df, meta, keep=6):
    rets = df.pct_change(fill_method=None)
    market = {"시장 전체": list(df.columns)}
    groups, sub = {}, {}
    for s in df.columns:
        groups.setdefault(meta[s]["s"], []).append(s)
        sub.setdefault(meta[s]["i"], []).append(s)
    groups.update({k: v for k, v in sub.items() if len(v) >= 4})  # 종목 4개 이상인 세부업종
    since = (df.index[-1] - timedelta(days=365)).strftime("%Y-%m-%d")
    mk = _detect(df, meta, rets, market, MARKET_THR, 1, 1, since)[-keep:]
    for s_thr, i_thr in TIERS:
        others = _detect(df, meta, rets, groups, 1, s_thr, i_thr, since)
        rest = []
        for o in others:  # 시장 급락과 10일 안에 시작한 업종 급락은 시장 급락에 합침
            m = next((m for m in mk if abs(_days(m["d"], o["d"])) <= 10), None)
            if m:
                m["g"] += [g for g in o["g"] if g not in m["g"]]
            else:
                rest.append(o)
        if len(mk) + len(rest) >= keep:
            break
    merged = mk + sorted(rest, key=lambda m: m["dd"])[: keep - len(mk)]
    merged.sort(key=lambda m: m["d"])
    level = lambda g: 0 if g == "시장 전체" else 1 if g in SECTOR_EN else 2
    names = load("ko_names.json", {})
    out = []
    for m in merged:
        kn = next((k for k in KNOWN if -7 <= _days(m["d"], k[0]) <= 14), None)
        name = kn[1] if kn else None
        gs = sorted(m["g"], key=level)
        en = "Stock market" if gs[0] == "시장 전체" else SECTOR_EN.get(gs[0], gs[0])
        ko = []
        for g in gs[:2]:
            if level(g) == 2 and names.get(g, g) == g:
                k = translate_ko(g)
                if k:
                    names[g] = k
            ko.append(names.get(g, g))
        who = "시장 전체" if gs[0] == "시장 전체" else "·".join(ko) + (" 등" if len(gs) > 2 else "")
        out.append({"d": m["d"], "name": name or f"{who} 급락", "who": who, "en": en, "dd": m["dd"],
                    "q": kn[2] if kn else "", "gk": gs[:3]})
    save("ko_names.json", names)
    try:
        hist_shocks(df, meta, rets, market, groups, names)
    except Exception as e:
        print("hist fail", e)
    cached = {(x["d"], x.get("q", "")): x.get("news") for x in load("shocks.json", []) if x.get("news")}
    for e in out:  # 이미 받아 둔 뉴스는 다시 받지 않음 (번역만 빠진 것은 다시 번역)
        news = cached.get((e["d"], e["q"])) or shock_news(e)
        miss = [it for it in news if not it.get("k")]
        if miss:
            for it, k in zip(miss, translate_many([it["t"] for it in miss])):
                it["k"] = k
        e["news"] = news
        cause = guess_cause(news)
        if cause and e["name"].endswith("급락"):  # 이름 없는 악재는 뉴스로 원인 추정해 붙이기
            e["name"] = f'{"·".join(cause)} ({e["who"]})'
            e["guess"] = True
        time.sleep(2)
    save("shocks.json", list(reversed(out)))
    print("shocks:", out)


# ---------- 3. SEC 분기 실적 ----------
def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def quarterly_series(facts, tags, unit="USD", ns="us-gaap", pure_only=False):
    """여러 태그를 합쳐 {분기말일: 3개월 값}.
    3개월 공시값을 우선 쓰고, 누적(6·9·12개월) 공시만 있으면 앞 누적값을 빼서 계산"""
    sec = facts.get("facts", {}).get(ns, {})
    pure, cum = {}, {}
    for tag in tags:
        units = sec.get(tag, {}).get("units", {}).get(unit, [])
        tp, tc = {}, {}
        for e in units:
            if "start" not in e or e.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                continue
            d = _days(e["start"], e["end"])
            if not 80 <= d <= 380:
                continue
            bucket = tp if d <= 100 else tc
            key = (e["start"], e["end"])
            prev = bucket.get(key)
            if prev is None or e.get("filed", "") > prev[1]:
                bucket[key] = (e["val"], e.get("filed", ""))
        for k, v in tp.items():
            pure.setdefault(k, v[0])
        for k, v in tc.items():
            cum.setdefault(k, v[0])
    out = {end: v for (start, end), v in pure.items()}
    if pure_only:  # 주식 수처럼 더할 수 없는 값은 3개월 공시값만 사용
        cum = {}
    # 같은 시작일의 누적값들: 3개월 값(있으면)과 누적값을 끝나는 날 순으로 이어 붙여 차이 계산
    by_start = {}
    for (st, en), v in list(pure.items()) + list(cum.items()):
        by_start.setdefault(st, {})[en] = v
    for st, series in by_start.items():
        ends = sorted(series)
        for i in range(1, len(ends)):
            e0, e1 = ends[i - 1], ends[i]
            if e1 not in out and 80 <= _days(e0, e1) <= 100:
                out[e1] = series[e1] - series[e0]
    clean = {}
    for e in sorted(out):
        if clean and _days(max(clean), e) < 45:
            continue
        clean[e] = out[e]
    return clean


def instant_series(facts, tag, unit="USD", ns="us-gaap"):
    units = facts.get("facts", {}).get(ns, {}).get(tag, {}).get("units", {}).get(unit, [])
    out = {}
    for e in units:
        if "start" in e or e.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A", "10-K405"):
            continue
        prev = out.get(e["end"])
        if prev is None or e.get("filed", "") > prev[1]:
            out[e["end"]] = (e["val"], e.get("filed", ""))
    return {k: v[0] for k, v in out.items()}


def fundamentals(meta):
    fund = load("fund.json", {})
    ok = 0
    for i, (sym, m) in enumerate(meta.items()):
        if not m.get("cik"):
            continue
        try:
            facts = json.loads(get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{m['cik']:010d}.json"))
        except Exception as e:
            print("SEC fail", sym, e)
            continue
        try:
            fund[sym] = _fund_one(facts)
            ok += 1
        except Exception as e:
            print("fund parse fail", sym, e)
        time.sleep(0.12)  # SEC 초당 10회 제한
    save("fund.json", fund)
    print(f"fund: {ok} updated")


def _fund_one(facts):
    if True:
        rev = quarterly_series(facts, REV_TAGS)
        op = quarterly_series(facts, OP_TAGS)
        ni = quarterly_series(facts, NI_TAGS)
        eps_rep = quarterly_series(facts, ["EarningsPerShareDiluted", "EarningsPerShareBasic"], "USD/shares")
        wsh = quarterly_series(facts, ["WeightedAverageNumberOfDilutedSharesOutstanding",
                                       "WeightedAverageNumberOfSharesOutstandingBasic"], "shares", pure_only=True)
        wsh = {e: v for e, v in wsh.items() if v and v > 0}
        wk = sorted(wsh)

        def shares_near(e):  # 그 분기 값이 없으면 120일 안의 가장 가까운 이전 분기 값
            prev = [k for k in wk if k <= e and _days(k, e) <= 120]
            return wsh[prev[-1]] if prev else None
        # 분기 EPS = 분기 순이익 ÷ 희석 주식 수 (누적값 차감으로 생기는 오류를 피함), 없으면 공시 EPS
        eps = {}
        for e in set(ni) | set(eps_rep):
            sh_e = shares_near(e) if e in ni else None
            if sh_e:
                eps[e] = round(ni[e] / sh_e, 4)
            elif e in eps_rep:
                eps[e] = eps_rep[e]
        ocf = quarterly_series(facts, ["NetCashProvidedByUsedInOperatingActivities",
                                       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"])
        capex = quarterly_series(facts, ["PaymentsToAcquirePropertyPlantAndEquipment",
                                         "PaymentsToAcquireProductiveAssets"])
        shares = instant_series(facts, "EntityCommonStockSharesOutstanding", "shares", "dei")
        if wsh:  # 표지 주식 수가 없거나 희석 주식 수와 크게 다르면 희석 주식 수 사용
            last_w = wsh[max(wsh)]
            last_s = shares[max(shares)] if shares else None
            if not last_s or not 0.5 < last_s / last_w < 2:
                shares = {e: v for e, v in wsh.items()}
        ends = sorted(set(rev) | set(op))
        ends = [e for e in ends if e >= "2017-01-01"]
        liab = instant_series(facts, "Liabilities")
        eq = instant_series(facts, "StockholdersEquity")
        tot = instant_series(facts, "LiabilitiesAndStockholdersEquity")
        for d, v in tot.items():  # 부채 합계를 따로 공시하지 않는 회사: 부채 = 총자산 - 자본
            if d not in liab and d in eq:
                liab[d] = v - eq[d]
        bal = sorted(e for e in set(liab) & set(eq) if e >= "2017-01-01")
        return {
            # [분기말, 매출, 영업이익, 순이익, 희석EPS, 영업현금흐름, 설비투자]
            "q": [[e, rev.get(e), op.get(e), ni.get(e), eps.get(e), ocf.get(e), capex.get(e)] for e in ends],
            "sh": [[d, v] for d, v in sorted(shares.items()) if d >= "2017-01-01"][-12:],
            "b": [[e, liab[e], eq[e]] for e in bal],
        }


# ---------- 4. 애널리스트 추정치 ----------
def estimates(meta):
    import yfinance as yf
    est = load("est.json", {})
    today = date.today()
    for sym in meta:
        try:
            tr = yf.Ticker(sym.replace(".", "-")).eps_trend
            if tr is None or tr.empty or "+1y" not in tr.index:
                continue
            row = tr.loc["+1y"]
            h = est.setdefault(sym, {})
            for col, back in (("90daysAgo", 90), ("60daysAgo", 60), ("30daysAgo", 30), ("7daysAgo", 7), ("current", 0)):
                v = row.get(col)
                if v is None or v != v:
                    continue
                d = (today - timedelta(days=back)).isoformat()
                if back == 0 or d not in h:  # 실제 기록이 우선
                    h[d] = round(float(v), 3)
        except Exception as e:
            print("est fail", sym, e)
        time.sleep(0.3)
    # 60일 넘은 기록은 주 1개로 줄이기
    cutoff = (today - timedelta(days=60)).isoformat()
    for sym, h in est.items():
        keep, seen = {}, set()
        for d in sorted(h, reverse=True):
            if d >= cutoff:
                keep[d] = h[d]
            else:
                wk = date.fromisoformat(d).isocalendar()[:2]
                if wk not in seen:
                    seen.add(wk)
                    keep[d] = h[d]
        est[sym] = dict(sorted(keep.items()))
    save("est.json", est)
    print(f"est: {len(est)} tickers")


# ---------- 5. 회사 소개 (한 줄, 한국어) ----------
def descriptions(meta):
    import re
    import yfinance as yf
    desc = load("desc.json", {})
    todo = [s for s in meta if not desc.get(s)][:150]  # 한 번에 150개까지 (나머지는 다음 날)
    eng = {}
    for sym in todo:
        try:
            txt = yf.Ticker(sym.replace(".", "-")).info.get("longBusinessSummary") or ""
        except Exception as e:
            print("desc fail", sym, e)
            continue
        first = re.split(r"(?<!\bInc)(?<!\bCorp)(?<!\bCo)(?<!\bLtd)(?<!\bU\.S)(?<!\bN\.A)(?<!\bNo)\. (?=[A-Z])",
                         txt.strip(), maxsplit=1)[0].strip().rstrip(".")
        if first:
            eng[sym] = first[:220]
        time.sleep(0.3)
    syms = list(eng)
    for i in range(0, len(syms), 12):  # 12개씩 묶어 번역
        chunk = syms[i:i + 12]
        for sym, ko in zip(chunk, translate_many([eng[s] for s in chunk])):
            if ko:
                desc[sym] = ko
        time.sleep(2)
    save("desc.json", desc)
    print(f"desc: {len(desc)} (new {len(eng)})")


# ---------- 6. 관심 종목 위험 정보 (실적 발표일·공매도·내부자·투자의견·뉴스) ----------
def _watch_set(meta):
    """1년 최고가보다 20% 이상 낮은 종목 + 최근 악재 기간에 20% 이상 빠진 종목"""
    pr = load("prices_recent.json", {"dates": [], "p": {}})
    dates, out = pr["dates"], set()
    n = len(dates)
    for s, p in pr["p"].items():
        last = [v for v in p[-252:] if v is not None]
        if last and last[-1] <= max(last) * 0.8:
            out.add(s)
    return sorted(out)


def _short_name(name):
    import re
    n = re.sub(r"\(.*?\)", " ", name)
    n = re.sub(r"[,.]|\b(Inc|Corp|Corporation|Company|Co|Ltd|plc|Holdings|Group|Incorporated|The)\b", " ", n)
    return " ".join(n.split()[:2]).strip()


def _relevant(title, sym, name):
    t = title.lower()
    sn = _short_name(name).lower()
    first = sn.split()[0] if sn else ""
    return (sym.lower() in t.replace("(", " ").replace(")", " ").split()) or (sn and sn in t) or \
        (len(first) >= 4 and first in t)


def _news_items(t, sym, name):
    items = []
    for it in (t.news or [])[:10]:
        c = it.get("content", it)
        title = c.get("title") or ""
        url = (c.get("canonicalUrl") or {}).get("url") or c.get("link") or ""
        prov = (c.get("provider") or {}).get("displayName") or c.get("publisher") or ""
        when = c.get("pubDate") or ""
        if not when and it.get("providerPublishTime"):
            when = datetime.fromtimestamp(it["providerPublishTime"], timezone.utc).isoformat()
        if title and _relevant(title, sym, name):  # 회사 이름·종목코드가 들어간 기사만
            items.append({"t": title, "u": url, "s": prov, "d": when[:10]})
    if len(items) < 3:  # 모자라면 구글 뉴스에서 회사 이름으로 최근 30일 검색
        today = date.today()
        for it in google_news(f'"{_short_name(name)}" stock', (today - timedelta(days=30)).isoformat(),
                              (today + timedelta(days=1)).isoformat(), "en"):
            if _relevant(it["t"], sym, name) and all(it["t"] != x["t"] for x in items):
                items.append(it)
            if len(items) >= 3:
                break
    return items[:3]


def _risk_one(args):
    sym, name = args
    import yfinance as yf
    t = yf.Ticker(sym.replace(".", "-"))
    r = {"u": date.today().isoformat()}
    try:
        cal = t.calendar or {}
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if ed:
            r["ed"] = str(ed[0])[:10]
    except Exception:
        pass
    try:
        info = t.info or {}
        if info.get("shortPercentOfFloat") is not None:
            r["sp"] = round(float(info["shortPercentOfFloat"]) * 100, 1)
    except Exception:
        pass
    try:
        ip = t.insider_purchases
        if ip is not None and not ip.empty:
            col0 = ip.columns[0]
            rows = {str(x).lower(): i for i, x in enumerate(ip[col0])}
            def val(key, col="Shares"):
                i = next((i for k, i in rows.items() if k.startswith(key)), None)
                return None if i is None else float(ip.iloc[i][col])
            r["ins"] = {"net": val("net shares"), "buy": val("purchases", "Trans"), "sell": val("sales", "Trans")}
    except Exception:
        pass
    try:
        ud = t.upgrades_downgrades
        if ud is not None and not ud.empty:
            ud = ud[ud.index >= (datetime.now() - timedelta(days=550))]
            r["ud"] = [[d.strftime("%Y-%m-%d"), a] for d, a in zip(ud.index, ud["Action"]) if a in ("up", "down")]
    except Exception:
        pass
    try:
        r["news"] = _news_items(t, sym, name)
    except Exception:
        pass
    return sym, r


def risk_info(meta, limit=None):
    """limit 없으면: 관심 종목 중 오늘 갱신 안 된 것 전부.
    limit 있으면: 전체 종목 중 가장 오래전에 갱신된 순으로 limit개 (장중 30분마다 조금씩 돌아가며 갱신)"""
    from concurrent.futures import ThreadPoolExecutor
    risk = load("risk.json", {})
    today = date.today().isoformat()
    if limit:
        watch = set(_watch_set(meta))
        order = sorted(meta, key=lambda s: (risk.get(s, {}).get("u", ""), s not in watch))
        todo_syms = [s for s in order if risk.get(s, {}).get("u") != today][:limit]
    else:
        todo_syms = [s for s in _watch_set(meta) if risk.get(s, {}).get("u") != today]
    with ThreadPoolExecutor(6) as ex:
        for sym, r in ex.map(_risk_one, [(s, meta[s]["n"]) for s in todo_syms]):
            old = {n["t"]: n.get("k") for n in risk.get(sym, {}).get("news", [])}
            for n in r.get("news", []):
                if old.get(n["t"]):
                    n["k"] = old[n["t"]]
            risk[sym] = r
    watch = todo_syms
    # 새 뉴스 제목만 12개씩 묶어 번역
    todo = [n for r in risk.values() for n in r.get("news", []) if not n.get("k")]
    for i in range(0, len(todo), 12):
        chunk = todo[i:i + 12]
        for n, k in zip(chunk, translate_many([n["t"] for n in chunk])):
            if k:
                n["k"] = k
        time.sleep(2)
    save("risk.json", risk)
    print(f"risk: {len(watch)} refreshed, {len(risk)} total")


def main():
    steps = sys.argv[1:] or ["meta", "live", "prices", "fund", "est", "risk", "desc"]
    meta = constituents()

    def step(name, fn, *args):  # 한 단계가 실패해도 나머지는 계속
        try:
            return fn(*args)
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"step {name} failed: {e}")

    if "meta" in steps:
        save("meta.json", {k: {"n": v["n"], "s": v["s"], "i": v["i"], "a": v["a"]} for k, v in meta.items()})
    if "live" in steps:
        step("live", live_quotes, list(meta))
    if "prices" in steps:
        df = prices(list(meta))  # 주가가 실패하면 저장할 것이 없으므로 그대로 중단
        step("shocks", find_shocks, df, meta)
    if "fund" in steps:
        step("fund", fundamentals, meta)
    if "est" in steps:
        step("est", estimates, meta)
    if "risk" in steps:
        step("risk", risk_info, meta)
    if "risk40" in steps:
        step("risk40", risk_info, meta, 40)
    if "desc" in steps:
        step("desc", descriptions, meta)
    save("updated.json", {"t": datetime.now(timezone.utc).isoformat(timespec="minutes")})


if __name__ == "__main__":
    main()
