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


CHANGED = set()  # 이번 실행에서 실제로 내용이 바뀐 파일
STATUS = {}  # 단계별 성공·실패
TR_FAIL = [0]  # 번역 실패 건수 (실패하면 영어 원문을 그대로 보여 줌)


def save(name, obj):
    """내용이 그대로면 파일을 다시 쓰지 않는다 → 바뀐 것이 없으면 커밋도 생기지 않음"""
    txt = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    path = os.path.join(DATA, name)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == txt:
                return
    except Exception:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(txt)
    CHANGED.add(name)


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
    import pandas as pd
    import yfinance as yf
    ymap = {s: s.replace(".", "-") for s in symbols}
    raw = yf.download(list(ymap.values()), start="2019-06-01", auto_adjust=True, actions=True,
                      progress=False, threads=True)
    df = raw["Close"].dropna(how="all")
    # 최근 1년 주당 배당금 합계 (연 배당률 = 이 값 ÷ 현재가)
    try:
        dv = raw["Dividends"].fillna(0)
        last1y = dv[dv.index > dv.index[-1] - pd.Timedelta(days=365)].sum()
        save("div.json", {s: round(float(last1y[y]), 4) for s, y in ymap.items()
                          if y in last1y.index and last1y[y] > 0})
    except Exception as e:
        print("div fail", e)
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
    raw = yf.download(list(ymap.values()), period="5d", interval="5m", prepost=True,
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
    if not q:
        raise RuntimeError("현재가를 하나도 받지 못함")
    if load("live.json", {}).get("q") == q:  # 장이 닫혀 값이 그대로면 저장하지 않음
        print("live: unchanged")
        return
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
    j = json.loads(get(url))
    txt = (j.get("responseData") or {}).get("translatedText", "") or ""
    # 하루 한도 초과 등은 오류 문장을 '번역'으로 돌려주므로 실패로 처리 → 화면에는 영어 원문
    if str(j.get("responseStatus")) != "200" or "MYMEMORY" in txt.upper() or "QUERY LENGTH LIMIT" in txt.upper():
        raise RuntimeError(f"mymemory {j.get('responseStatus')}: {txt[:80]}")
    return txt


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
    TR_FAIL[0] += 1
    for t in texts:
        try:
            res.append(_mymemory(t).strip())
        except Exception as e:
            print("translate(mymemory) fail", e)
            TR_FAIL[0] += 1
            res.append("")
        time.sleep(1)
    return res


def translate_ko(text):
    return (translate_many([text]) or [""])[0]


# 업종 이름 → 뉴스 제목에서 찾을 영어 단어 (원인 뉴스가 그 업종 이야기인지 확인용)
GROUP_WORDS = {
    "Information Technology": ["tech", "technology", "software", "chip", "semiconductor"],
    "Health Care": ["health", "healthcare", "drug", "pharma", "biotech", "medical", "insurer"],
    "Financials": ["bank", "banks", "lender", "financial", "insurer", "credit card", "card issuer", "brokerage", "asset manager"],
    "Consumer Discretionary": ["retail", "retailer", "consumer", "automaker", "travel", "restaurant"],
    "Communication Services": ["media", "telecom", "streaming", "advertising", "social media"],
    "Industrials": ["industrial", "airline", "defense", "aerospace", "transport", "freight"],
    "Consumer Staples": ["consumer staples", "grocer", "food", "beverage", "tobacco"],
    "Energy": ["oil", "energy", "crude", "gas", "drilling"],
    "Utilities": ["utility", "utilities", "power", "electric"],
    "Real Estate": ["reit", "real estate", "property", "landlord"],
    "Materials": ["chemical", "miner", "mining", "steel", "metals", "gold", "copper"],
    "Transaction & Payment Processing Services": ["payment", "payments", "fintech", "credit card", "card", "swipe fee", "interchange"],
    "Consumer Finance": ["credit card", "card", "lender", "consumer finance"],
    "IT Consulting & Other Services": ["it services", "consulting", "consultancy", "outsourcing", "tech services"],
    "Application Software": ["software", "saas", "cloud"],
    "Systems Software": ["software", "cloud", "cybersecurity"],
    "Managed Health Care": ["health insurer", "insurer", "insurers", "managed care", "medicare", "medicaid", "health plan"],
    "Semiconductors": ["chip", "chips", "chipmaker", "semiconductor", "memory", "ai chip"],
    "Semiconductor Materials & Equipment": ["chip", "chips", "chipmaker", "semiconductor", "memory", "chip equipment", "chip gear"],
    "Construction & Engineering": ["construction", "engineering", "data center", "infrastructure", "ai infrastructure"],
    "Electrical Components & Equipment": ["electrical", "power equipment", "data center", "grid"],
}
GROUP_QUERY = {  # 검색어로 쓸 짧은 이름 (없으면 업종 이름을 줄여서 사용)
    "Transaction & Payment Processing Services": "payment", "IT Consulting & Other Services": "IT services",
    "Managed Health Care": "health insurer", "Semiconductor Materials & Equipment": "chip equipment",
    "Semiconductors": "chip", "Application Software": "software", "Consumer Finance": "credit card",
    "Information Technology": "tech", "Health Care": "healthcare", "Financials": "bank",
}
MARKET_WORDS = ["stock market", "stocks", "wall street", "s&p 500", "s&p", "nasdaq", "dow", "markets", "equities"]
FALL_WORDS = ["slump", "tumble", "sink", "sank", "drop", "rout", "crash", "selloff", "sell-off", "plunge", "fall",
              "fell", "slide", "slid", "hit", "dip", "sell", "slam", "pressure", "worr", "fear", "concern", "threat",
              "losses", "lower", "skid", "correction", "rattle", "jolt", "shock", "down"]
# 원인 설명이 아닌 투자 조언·전망·회고 글
ADVICE_WORDS = ["where to invest", "history says", "smartest move", "outlook", "how to ", "should you", "best stocks",
                "here's what", "here’s what", "what happened after", "could win", "buying opportunity", "to buy",
                "what investors should", "investors should do", "protect their money", "spin-off", "spinoff"]
NEWS_THR = 3          # 이 점수보다 낮으면 '원인 뉴스 확인 안 됨'
NEWS_DAYS = 5         # 악재 시작일 ±5일 안의 기사만
NEWS_VER = 2          # 뉴스 고르는 방식이 바뀌면 올림 → 예전에 받아 둔 뉴스 다시 받기
STOP = {"services", "other", "and", "&", "equipment", "products", "management", "development", "integrated",
        "diversified", "specialized", "processing", "transaction", "components"}


def _gwords(g):
    import re
    en = SECTOR_EN.get(g, g)
    ws = list(GROUP_WORDS.get(en, []))
    for w in re.findall(r"[A-Za-z]+", en):  # 업종 이름 자체의 단어 (예: Biotechnology, Airlines)
        w = w.lower()
        if len(w) >= 5 and w not in STOP and w not in ws:
            ws.append(w.rstrip("s") if w.endswith("s") and not w.endswith("ss") else w)
    return ws


def _has(text, w):
    import re
    return re.search(r"(?<![a-z])" + re.escape(w), text) is not None


def _news_date(it):
    try:
        return datetime.strptime(it.get("d", "").strip(), "%d %b %Y").date()
    except Exception:
        return None


def affected(df, meta, groups, gk, d, t, top=20):
    """그 악재 때 함께 빠진 종목 (악재 전날 대비 바닥까지 하락률이 큰 순)"""
    import pandas as pd
    d0, t0 = pd.Timestamp(d), min(pd.Timestamp(t), pd.Timestamp(d) + pd.Timedelta(days=30))
    pre = df[df.index < d0]
    if pre.empty:
        return []
    base, low = pre.iloc[-1], df[(df.index >= d0) & (df.index <= t0)].min()
    first = df[df.index >= d0].iloc[:3].min()  # 시작 사흘 동안의 하락 (누가 먼저 빠졌는지)
    syms = set()
    for g in gk:
        syms |= set(groups.get(g, []))
    res = []
    for s in syms:
        if s in base.index and base[s] and base[s] == base[s] and low[s] == low[s]:
            r = float(low[s] / base[s] - 1)
            if r <= -0.08:
                g = next((g for g in gk if s in groups.get(g, [])), "")
                res.append({"s": s, "n": _short_name(meta[s]["n"]), "g": g, "r": round(r * 100, 1),
                            "r3": round(float(first[s] / base[s] - 1) * 100, 1)})
    res.sort(key=lambda x: x["r"])
    return res[:top]


def score_news(it, e):
    """뉴스 제목이 이 악재의 원인 설명과 얼마나 관련 있는지 점수 (낮으면 버림)"""
    t = " " + it.get("t", "").lower() + " "
    raw = it.get("t", "")
    nd, d = _news_date(it), date.fromisoformat(e["d"])
    if nd and abs((nd - d).days) > NEWS_DAYS:
        return -9
    market = e["gk"][0] == "시장 전체"
    grp = {w for g in e["gk"] for w in _gwords(g) if _has(t, w)}
    import re
    cos = [a for a in e.get("hit", []) if len(a["n"]) >= 3 and
           re.search(r"(?<![A-Za-z])" + re.escape(a["n"].split()[0] if len(a["n"].split()[0]) >= 4 else a["n"]) + r"(?![a-z])", raw)]
    drv = e.get("drv")
    if market:
        rel = (2 if any(_has(t, w) for w in MARKET_WORDS) else 0) + (2 if len(cos) >= 2 else 0)
    else:
        rel = 2 * min(len(grp), 2)
        if len(cos) >= 2:
            rel += 2 + min(len(cos) - 2, 2)
        elif cos and (cos[0]["s"] == drv or grp):  # 한 회사만 나온 기사는 그 회사가 주도주이거나 업종 이야기일 때만
            rel += 1 if grp else 2
        if not grp and len(cos) < 2 and not (cos and cos[0]["s"] == drv):
            return 0
    if rel == 0:
        return 0
    sc = rel
    fall = any(_has(t, w) for w in FALL_WORDS)
    if fall:
        sc += 1
    elif any(_has(t, w) for w in ("rebound", "rally", "rallies", "soar", "surge", "record high", "gain")):
        sc -= 2  # 반등 기사는 원인 설명이 아님
    if any(k in t for keys, _ in CAUSES for k in keys):
        sc += 1
    if any(w in t for w in ADVICE_WORDS):
        sc -= 3
    return sc


def shock_news(e, n=5):
    """악재 시작일 ±5일 영어 뉴스 중 함께 빠진 업종·종목과 관련 있는 원인 기사만 골라 번역"""
    d = date.fromisoformat(e["d"])
    d_from, d_to = (d - timedelta(days=NEWS_DAYS + 1)).isoformat(), (d + timedelta(days=NEWS_DAYS + 1)).isoformat()
    market = e["gk"][0] == "시장 전체"
    if market:
        queries = ["stock market sell-off", "stocks plunge Wall Street", "S&P 500 falls why"]
    else:
        queries = []
        for g in e["gk"][:2]:
            en = SECTOR_EN.get(g, g)
            k = GROUP_QUERY.get(en) or " ".join(w for w in en.split() if w.lower() not in STOP and w != "&")
            queries += [f"{k} stocks fall", f"{k} stocks sell-off why"]
        main = [a["n"] for a in e.get("hit", []) if a.get("g") == e["gk"][0]][:3]  # 주된 업종에서 함께 빠진 종목
        names = [a["n"] for a in e.get("hit", [])[:3]]
        for i, ns in enumerate(x for x in (main, names) if x):
            q = " ".join(ns) + " shares fall"
            if q not in queries:
                queries.insert(1 + i * 2, q)
    if e.get("q"):
        queries = [e["q"]] + queries
    cand, seen = [], set()
    for q in queries:
        for it in google_news(q, d_from, d_to, "en"):
            key = it["t"][:30].lower()
            if key in seen:
                continue
            seen.add(key)
            it["sc"] = score_news(it, e)
            if it["sc"] >= NEWS_THR:
                cand.append(it)
        time.sleep(1)
    cand.sort(key=lambda it: -it["sc"])
    out = cand[:n]
    if out:
        for it, k in zip(out, translate_many([it["t"] for it in out])):
            it["k"] = k
        return out
    if market:  # 시장 전체 악재인데 영어 뉴스가 없으면 한국어 뉴스로
        for it in google_news("뉴욕증시 급락", d_from, d_to, "ko"):
            nd = _news_date(it)
            if (not nd or abs((nd - d).days) <= NEWS_DAYS) and any(k in it["t"] for k in ("뉴욕증시", "미 증시", "미국 증시")) \
                    and any(k in it["t"] for k in ("급락", "폭락", "하락", "추락")):
                it["k"], it["sc"] = it["t"], NEWS_THR
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
    (("rate cap", "interest cap", "cap on credit", "credit card cap", "credit-card cap", "card rate", "card interest",
      "swipe fee", "interchange"), "카드 금리 상한·규제"),
    (("tariff", "trade war", "trade deal", "trade tension"), "관세·무역분쟁"),
    (("iran", "hormuz", "israel", "missile", "war ", "invasion", "ukraine", "russia"), "전쟁·지정학"),
    (("oil", "crude", "opec"), "유가"),
    (("fed ", "fed's", "federal reserve", "powell", "rate hike", "interest rate"), "금리·연준"),
    (("inflation", "cpi"), "인플레이션"),
    (("bond", "yield", "treasur"), "채권금리"),
    ((" ai ", "ai-", "ai spending", "artificial intelligence"), "AI 우려"),
    (("chip", "semiconductor", "nvidia"), "반도체"),
    (("export control", "export curb", "export ban", "china"), "중국·수출규제"),
    (("bank", "lender", "credit crunch", "credit market", "private credit", "credit risk"), "은행·신용"),
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
    for keys, label in CAUSES:  # 앞 항목에서 쓴 단어는 지워서 두 번 세지 않음 (예: credit card interest rate cap)
        c = sum(text.count(k) for k in keys)
        for k in keys:
            text = text.replace(k, " ")
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
        hit = affected(df, meta, groups if gs[0] != "시장 전체" else market, gs[:3], m["d"], m["t"])
        drv = min(hit, key=lambda a: a["r3"])["s"] if hit else None  # 시작 사흘 동안 가장 크게 빠진 종목 = 주도주
        out.append({"d": m["d"], "name": name or f"{who} 급락", "who": who, "en": en, "dd": m["dd"],
                    "q": kn[2] if kn else "", "gk": gs[:3], "t": m["t"], "drv": drv,
                    "hit": [{"s": a["s"], "n": a["n"], "g": a["g"], "r": a["r"], "r3": a["r3"]} for a in hit[:12]]})
    save("ko_names.json", names)
    try:
        hist_shocks(df, meta, rets, market, groups, names)
    except Exception as e:
        print("hist fail", e)
    # 이미 받아 둔 뉴스는 다시 받지 않음 (뉴스 고르는 방식이 바뀌었거나 그때 원인 뉴스를 못 찾았으면 다시 받기)
    cached = {(x["d"], x.get("q", "")): x for x in load("shocks.json", [])
              if x.get("nv") == NEWS_VER and x.get("news")}
    for e in out:
        old = cached.get((e["d"], e["q"]))
        news = old["news"] if old else shock_news(e)
        miss = [it for it in news if not it.get("k")]
        if miss:
            for it, k in zip(miss, translate_many([it["t"] for it in miss])):
                it["k"] = k
        e["news"], e["nv"] = news, NEWS_VER
        e["nc"] = not news  # 원인 뉴스 확인 안 됨
        cause = guess_cause(news)
        if cause and e["name"].endswith("급락"):  # 이름 없는 악재는 관련 뉴스로 원인 추정해 붙이기
            e["name"] = f'{"·".join(cause)} ({e["who"]})'
            e["guess"] = True
        if not old:
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
            r = fn(*args)
            STATUS[name] = "ok"
            return r
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"step {name} failed: {e}")
            STATUS[name] = "fail"

    if "meta" in steps:
        save("meta.json", {k: {"n": v["n"], "s": v["s"], "i": v["i"], "a": v["a"]} for k, v in meta.items()})
    if "live" in steps:
        step("live", live_quotes, list(meta))
    if "prices" in steps:
        try:
            df = prices(list(meta))
            STATUS["prices"] = "ok"
        except Exception:
            STATUS["prices"] = "fail"
            _status()
            raise  # 주가가 실패하면 저장할 것이 없으므로 그대로 중단
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
    _status()


def _status():
    """갱신 시각·단계별 성공 여부. 자료가 바뀌었거나 성공/실패 상태가 달라졌을 때만 새로 씀 (빈 커밋 방지)"""
    old = load("updated.json", {})
    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    st = dict(old.get("st", {}))
    run = dict(STATUS)
    if any(k in run for k in ("shocks", "risk", "risk40", "desc")):
        run["translate"] = "fail" if TR_FAIL[0] else "ok"
    for k, v in run.items():
        if k not in st or st[k][0] != v:
            st[k] = [v, now]  # [상태, 그 상태가 된 시각]
    fails = sorted(k for k, v in st.items() if v[0] == "fail")
    if not CHANGED and st == old.get("st"):
        print("no data change → 커밋 없음")
        return
    save("updated.json", {"t": now if CHANGED or "t" not in old else old["t"], "st": st, "fails": fails})


if __name__ == "__main__":
    main()
