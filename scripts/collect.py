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
        }
    return out


# ---------- 2. 주가 ----------
def prices(symbols):
    import yfinance as yf
    ymap = {s: s.replace(".", "-") for s in symbols}
    df = yf.download(list(ymap.values()), start="2019-06-01", auto_adjust=True,
                     progress=False, threads=True)["Close"]
    df = df.dropna(how="all")
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
    print(f"prices: {len(recent['p'])} tickers, {len(dates)} days")
    return df.rename(columns={y: s for s, y in ymap.items()})


# ---------- 2-1. 악재 자동 찾기 ----------
MARKET_THR, SECTOR_THR = 0.10, 0.15  # 2달 안에 시장 중간값 10%↓ 또는 업종 중간값 15%↓
KNOWN = [  # 이름 붙이기용 (날짜 ±25일 안이면 이 이름 사용)
    ("2020-02-21", "코로나19 팬데믹"), ("2022-01-04", "금리 인상 충격"),
    ("2022-05-05", "금리 인상·인플레이션"), ("2023-03-09", "SVB 은행 파산"),
    ("2024-08-01", "엔캐리 청산"), ("2025-04-03", "트럼프 상호관세"),
]


NEWS_TERM = {
    "시장 전체": "뉴욕증시", "정보기술": "기술주", "헬스케어": "헬스케어주", "금융": "은행주",
    "경기소비재": "소비재주", "커뮤니케이션": "통신주", "산업재": "산업재", "필수소비재": "필수소비재",
    "에너지": "에너지주", "유틸리티": "유틸리티주", "부동산": "리츠", "소재": "소재주",
}


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


def shock_news(e, n=4):
    """악재 날짜 앞뒤 뉴스 제목을 받아와 원인 확인용으로 붙이기"""
    d = date.fromisoformat(e["d"])
    d_from, d_to = (d - timedelta(days=3)).isoformat(), (d + timedelta(days=7)).isoformat()
    first = e["who"].split("·")[0].replace(" 등", "")
    term = NEWS_TERM.get(first, "뉴욕증시")
    out, seen = [], set()
    for q, lang in ((f"{term} 급락", "ko"), ("뉴욕증시 급락 이유", "ko"),
                    ("stocks plunge" if first == "시장 전체" else f"{first} stocks plunge", "en")):
        for it in google_news(q, d_from, d_to, lang):
            key = it["t"][:25]
            if key in seen:
                continue
            seen.add(key)
            out.append(it)
            if len(out) >= n:
                return out
        time.sleep(1)
    return out


def find_shocks(df, meta, keep=6):
    import pandas as pd
    rets = df.pct_change(fill_method=None)
    groups = {"시장 전체": list(df.columns)}
    for s in df.columns:
        groups.setdefault(meta[s]["s"], []).append(s)
    events = []
    for g, cols in groups.items():
        idx = (1 + rets[cols].median(axis=1).fillna(0)).cumprod()
        thr = MARKET_THR if g == "시장 전체" else SECTOR_THR
        dates = idx.index
        dd = idx / idx.rolling("61D").max() - 1
        i = 0
        while i < len(idx):
            if dd.iloc[i] <= -thr:
                t = dates[i]
                win = idx[(dates >= t - pd.Timedelta(days=60)) & (dates <= t)]
                peak_d = win.idxmax()
                seg = idx[(dates > peak_d) & (dates <= peak_d + pd.Timedelta(days=60))]
                trough_d = seg.idxmin()
                # 급락이 실제로 시작된 날: 고점의 97% 위에 있던 마지막 날의 다음 거래일
                mid = idx[(dates >= peak_d) & (dates < trough_d)]
                start_d = mid[mid >= idx[peak_d] * 0.97].index[-1]
                shock = idx[dates > start_d].index[0]
                events.append({"d": shock.strftime("%Y-%m-%d"), "g": g, "t": trough_d.strftime("%Y-%m-%d"),
                               "dd": round(float(seg.min() / idx[peak_d] - 1) * 100, 1)})
                # 바닥 이후 지수가 회복되거나 20일 지날 때까지 건너뛰기
                i = max(i + 1, int((dates <= trough_d + pd.Timedelta(days=20)).sum()))
                continue
            i += 1
    events.sort(key=lambda e: e["d"])
    merged = []
    for e in events:  # 30일 안에 겹치는 업종 급락은 하나로
        if merged and (_days(merged[-1]["d"], e["d"]) <= 30 or e["d"] <= merged[-1]["t"]):
            m = merged[-1]
            if e["g"] not in m["g"]:
                m["g"].append(e["g"])
            m["dd"] = min(m["dd"], e["dd"])
            m["t"] = max(m["t"], e["t"])
        else:
            merged.append({"d": e["d"], "g": [e["g"]], "dd": e["dd"], "t": e["t"]})
    out = []
    for m in merged[-keep:]:
        name = next((n for d, n in KNOWN if abs(_days(d, m["d"])) <= 25), None)
        gs = m["g"]
        who = "시장 전체" if "시장 전체" in gs else "·".join(gs[:2]) + (" 등" if len(gs) > 2 else "")
        out.append({"d": m["d"], "name": name or f"{who} 급락", "who": who, "dd": m["dd"]})
    for e in out:
        e["news"] = shock_news(e)
    save("shocks.json", list(reversed(out)))
    print("shocks:", out)


# ---------- 3. SEC 분기 실적 ----------
def _days(a, b):
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def quarterly_series(facts, tags):
    """여러 태그를 합쳐 {분기말일: 값} (3개월 값, 4분기는 연간-9개월로 계산)"""
    gaap = facts.get("facts", {}).get("us-gaap", {})
    q, ytd, fy = {}, {}, {}
    for tag in tags:
        units = gaap.get(tag, {}).get("units", {}).get("USD", [])
        tq, tytd, tfy = {}, {}, {}
        for e in units:
            if "start" not in e or e.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
                continue
            d = _days(e["start"], e["end"])
            key = (e["start"], e["end"])
            bucket = tq if 80 <= d <= 100 else tytd if 260 <= d <= 285 else tfy if 350 <= d <= 380 else None
            if bucket is None:
                continue
            prev = bucket.get(key)
            if prev is None or e.get("filed", "") > prev[1]:
                bucket[key] = (e["val"], e.get("filed", ""))
        for src, dst in ((tq, q), (tytd, ytd), (tfy, fy)):
            for k, v in src.items():
                dst.setdefault(k, v[0])  # 앞 태그 우선
    out = {end: v for (start, end), v in q.items()}
    for (s, e), v in fy.items():
        if e in out:
            continue
        nine = [val for (s2, e2), val in ytd.items() if s2 == s and e2 < e]
        if nine:
            out[e] = v - nine[0]
        else:
            three = sorted([(e2, val) for (s2, e2), val in q.items() if s2 >= s and e2 < e])
            if len(three) == 3:
                out[e] = v - sum(x[1] for x in three)
    # 같은 분기가 날짜만 조금 다르게(52/53주) 중복되는 경우 정리
    clean = {}
    for e in sorted(out):
        if clean and _days(max(clean), e) < 45:
            continue
        clean[e] = out[e]
    return clean


def instant_series(facts, tag):
    units = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
    out = {}
    for e in units:
        if "start" in e or e.get("form") not in ("10-Q", "10-K", "10-Q/A", "10-K/A"):
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
        rev = quarterly_series(facts, REV_TAGS)
        op = quarterly_series(facts, OP_TAGS)
        ni = quarterly_series(facts, NI_TAGS)
        ends = sorted(set(rev) | set(op))
        ends = [e for e in ends if e >= "2017-01-01"]
        liab = instant_series(facts, "Liabilities")
        eq = instant_series(facts, "StockholdersEquity")
        bal = sorted(e for e in set(liab) & set(eq) if e >= "2017-01-01")
        fund[sym] = {
            "q": [[e, rev.get(e), op.get(e), ni.get(e)] for e in ends],
            "b": [[e, liab[e], eq[e]] for e in bal],
        }
        ok += 1
        time.sleep(0.12)  # SEC 초당 10회 제한
    save("fund.json", fund)
    print(f"fund: {ok} updated")


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


def main():
    steps = sys.argv[1:] or ["meta", "prices", "fund", "est"]
    meta = constituents()
    if "meta" in steps:
        save("meta.json", {k: {"n": v["n"], "s": v["s"], "i": v["i"]} for k, v in meta.items()})
    if "prices" in steps:
        df = prices(list(meta))
        find_shocks(df, meta)
    if "fund" in steps:
        fundamentals(meta)
    if "est" in steps:
        estimates(meta)
    save("updated.json", {"t": datetime.now(timezone.utc).isoformat(timespec="minutes")})


if __name__ == "__main__":
    main()
