"""10년 백테스트 (GitHub Actions에서 한 번 실행)

검색기 기본 조건 그대로:
  악재 전 2달 최고가 → 악재 후 2달 최저가 30%↑ 하락, 악재 2달 뒤(진입일) 최고가의 90% 미만,
  업종 중간값 15%↑ 동반 하락, 악재 전 분기 매출 +10%↑·영업이익 증가, 부채비율 300% 이하(금융·부동산 제외),
  악재 전 1년 순이익 흑자, 악재 당시 S&P 500 구성종목만(편입·편출 기록 사용, 편출 종목 포함 — 주가가 없는 종목 수는 결과에 기록).
진입일에 후보를 같은 금액으로 사서 3·6·12개월 보유했을 때 수익률을 계산하고 연도별로 묶는다.
결과: docs/data/backtest10y.json
"""
import json
import os
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import collect as C  # noqa: E402

START = "2015-06-01"


def fund_all(meta):
    out = {}
    for sym, m in meta.items():
        if not m.get("cik"):
            continue
        try:
            facts = json.loads(C.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{m['cik']:010d}.json"))
            rev = C.quarterly_series(facts, C.REV_TAGS)
            op = C.quarterly_series(facts, C.OP_TAGS)
            ni = C.quarterly_series(facts, C.NI_TAGS)
            liab = C.instant_series(facts, "Liabilities")
            eq = C.instant_series(facts, "StockholdersEquity")
            tot = C.instant_series(facts, "LiabilitiesAndStockholdersEquity")
            for d, v in tot.items():
                if d not in liab and d in eq:
                    liab[d] = v - eq[d]
            out[sym] = {"q": [[e, rev.get(e), op.get(e), ni.get(e)] for e in sorted(set(rev) | set(op))],
                        "b": [[e, liab[e], eq[e]] for e in sorted(set(liab) & set(eq))]}
        except Exception as e:
            print("fund fail", sym, e)
        time.sleep(0.12)
    return out


def yoy(qs, col):
    if not qs:
        return None
    cur = qs[-1]
    t = date.fromisoformat(cur[0])
    prev = next((x for x in qs if 330 < (t - date.fromisoformat(x[0])).days < 400), None)
    if not prev or cur[col] is None or prev[col] in (None, 0):
        return None
    if prev[col] > 0:
        return (cur[col] / prev[col] - 1) * 100
    return 999 if cur[col] > prev[col] else -999


def fund_check(f, shock):
    out = {"rev": None, "op": None, "ni": None, "de": None}
    if not f:
        return out
    qs = [x for x in f["q"] if x[0] < shock]
    if qs:
        out["rev"], out["op"] = yoy(qs, 1), yoy(qs, 2)
        l4 = qs[-4:]
        if len(l4) == 4 and all(x[3] is not None for x in l4) and \
                (date.fromisoformat(l4[3][0]) - date.fromisoformat(l4[0][0])).days < 300:
            out["ni"] = sum(x[3] for x in l4)
    b = [x for x in f["b"] if x[0] < shock]
    if b and b[-1][2] > 0:
        out["de"] = b[-1][1] / b[-1][2] * 100
    return out


PIT = os.path.join(os.path.dirname(__file__), "sp500_pit.json")
YSEC = {"Technology": "정보기술", "Healthcare": "헬스케어", "Financial Services": "금융", "Consumer Cyclical": "경기소비재",
        "Consumer Defensive": "필수소비재", "Industrials": "산업재", "Energy": "에너지", "Utilities": "유틸리티",
        "Real Estate": "부동산", "Basic Materials": "소재", "Communication Services": "커뮤니케이션"}


def pit_members():
    """그 시점 S&P 500 구성종목 (편입·편출 기록: github.com/fja05680/sp500)."""
    with open(PIT, encoding="utf-8") as f:
        p = json.load(f)
    return p["iv"], p["through"]


def is_member(iv, through, meta, s, d):
    if d > through:  # 기록 이후 날짜는 현재 명단으로
        return s in meta and (not meta[s].get("a") or meta[s]["a"] <= d)
    return any(a <= d and (b is None or d < b) for a, b in iv.get(s, []))


def removed_meta(syms):
    """지금은 S&P 500에 없는 과거 편입 종목의 업종·CIK (주가가 남아 있는 종목만 구해짐)"""
    import yfinance as yf
    out = {}
    try:
        tk = json.loads(C.get("https://www.sec.gov/files/company_tickers.json"))
        cik = {v["ticker"].upper(): int(v["cik_str"]) for v in tk.values()}
    except Exception as e:
        print("sec tickers fail", e)
        cik = {}
    for s in syms:
        info = {}
        try:
            info = yf.Ticker(s.replace(".", "-")).info or {}
        except Exception:
            pass
        out[s] = {"n": info.get("shortName") or s, "s": YSEC.get(info.get("sector", ""), "미상"),
                  "i": info.get("industry") or "미상", "cik": cik.get(s.replace(".", "-").upper()), "a": "", "removed": True}
        time.sleep(0.2)
    return out


def main():
    import pandas as pd
    import yfinance as yf
    meta = C.constituents()
    iv, through = pit_members()
    ever = sorted(t for t, ws in iv.items() if any(b is None or b >= START for a, b in ws))
    removed = [t for t in ever if t not in meta]
    syms = list(meta) + removed
    ymap = {s: s.replace(".", "-") for s in syms}
    raw = yf.download(list(ymap.values()) + ["^GSPC"], start=START, auto_adjust=True, progress=False, threads=True)["Close"]
    raw = raw.dropna(how="all")
    spx = raw["^GSPC"]
    have = [s for s in syms if ymap[s] in raw.columns and raw[ymap[s]].notna().sum() > 20]
    no_price = [s for s in removed if s not in have]
    meta.update(removed_meta([s for s in removed if s in have]))
    df = raw[[ymap[s] for s in have]].copy()
    df.columns = have
    print("prices", df.shape, "removed", len(removed), "no price", len(no_price))
    fund = fund_all(meta)
    print("fund", len(fund))

    # 악재 찾기: 그날 구성종목 기준이 이상적이지만 사이트와 같게 현재+과거 종목 전체로 찾는다
    rets = df.pct_change(fill_method=None)
    market = {"시장 전체": list(df.columns)}
    groups, sub = {}, {}
    for s in df.columns:
        if meta[s]["s"] == "미상":
            continue
        groups.setdefault(meta[s]["s"], []).append(s)
        sub.setdefault(meta[s]["i"], []).append(s)
    groups.update({k: v for k, v in sub.items() if len(v) >= 4})
    mk = C._detect(df, meta, rets, market, 0.08, 1, 1, "2016-01-01")
    for o in C._detect(df, meta, rets, groups, 1, 0.15, 0.25, "2016-01-01"):
        m = next((m for m in mk if abs(C._days(m["d"], o["d"])) <= 10), None)
        if m:
            m["g"] += [g for g in o["g"] if g not in m["g"]]
        else:
            mk.append(o)
    shocks = sorted(mk, key=lambda m: m["d"])
    print("shocks", len(shocks))

    dates = df.index
    last = len(dates) - 1
    H = {"3m": 63, "6m": 126, "1y": 252}
    picks, miss_at = [], {}
    for sh in shocks:
        d = pd.Timestamp(sh["d"])
        a = int((dates < d - pd.Timedelta(days=60)).sum())
        i0 = int((dates < d).sum()) - 1
        b = int((dates <= d + pd.Timedelta(days=60)).sum()) - 1
        entry = int((dates <= d + pd.Timedelta(days=61)).sum()) - 1
        if i0 < 1 or entry >= last:
            continue
        mem = [s for s in df.columns if is_member(iv, through, meta, s, sh["d"])]
        miss_at[sh["d"]] = sum(1 for s in no_price if is_member(iv, through, meta, s, sh["d"]))
        win_pre, win_post = df.iloc[a:i0 + 1][mem], df.iloc[i0 + 1:b + 1][mem]
        high, low = win_pre.max(), win_post.min()
        drop = (low / high - 1) * 100
        sec_med = drop.groupby(lambda s: meta[s]["s"]).median()
        for s in mem:
            if pd.isna(drop.get(s)) or drop[s] > -30:
                continue
            e_px = df[s].iloc[:entry + 1].dropna()
            if e_px.empty:
                continue
            e_px = e_px.iloc[-1]
            if e_px / high[s] * 100 >= 90:
                continue
            if meta[s]["s"] == "미상" or not sec_med.get(meta[s]["s"], 0) <= -15:
                continue
            f = fund_check(fund.get(s), sh["d"])
            fin = meta[s]["s"] in ("금융", "부동산")
            # 자료가 없는 조건은 일단 통과시키고 unk 표시 (사이트에서 '자료 없음 통과'를 켰을 때와 같음)
            if f["rev"] is not None and f["rev"] < 10:
                continue
            if f["op"] is not None and f["op"] < 0:
                continue
            if not fin and f["de"] is not None and f["de"] > 300:
                continue
            if f["ni"] is not None and f["ni"] <= 0:
                continue
            unk = f["rev"] is None or f["op"] is None or f["ni"] is None or (not fin and f["de"] is None)
            px = df[s]
            pre_px = px.iloc[:i0 + 1].dropna()
            mom = float(pre_px.iloc[-1] / pre_px.iloc[-253] - 1) * 100 if len(pre_px) > 253 else None
            vol = float(pre_px.pct_change().tail(60).std() * (252 ** 0.5) * 100) if len(pre_px) > 61 else None
            w = px.iloc[:entry + 1].dropna().tail(20)
            above20 = bool(len(w) == 20 and w.iloc[-1] > w.mean())
            low_i = int(df[s].iloc[i0 + 1:b + 1].values.argmin()) + i0 + 1
            row = {"shock": sh["d"], "s": s, "sec": meta[s]["s"], "sub": meta[s]["i"], "drop": round(float(drop[s]), 1),
                   "market": "시장 전체" in sh["g"], "shock_dd": sh["dd"], "sec_med": round(float(sec_med[meta[s]["s"]]), 1),
                   "rev": f["rev"], "op": f["op"], "de": f["de"], "ni_pos": (f["ni"] or 0) > 0, "unk": unk,
                   "removed": bool(meta[s].get("removed")),
                   "rec_entry": round(float(e_px / high[s] * 100), 1), "above20": above20,
                   "days_from_low": entry - low_i, "mom12": mom, "vol60": vol,
                   "rel_drop": round(float(drop[s] - sec_med[meta[s]["s"]]), 1)}
            fut = df[s].iloc[entry + 1:entry + 253].dropna()
            hit = fut[fut >= high[s]]
            row["full1y"] = entry + 252 <= last
            row["hit1y"] = bool(len(hit))  # 1년 안에 악재 전 최고가 회복
            row["hit_days"] = int(df.index.get_loc(hit.index[0]) - entry) if len(hit) else None
            if len(fut):
                row["mdd1y"] = round(float(fut.min() / e_px - 1) * 100, 1)  # 진입 후 1년 안 최대 하락
                pk = fut.idxmax()
                row["peak1y"] = round(float(fut.max() / e_px - 1) * 100, 1)  # 1년 안 최고 수익
                row["peak_days_low"] = int(df.index.get_loc(pk) - low_i)  # 악재 저점 → 1년 안 고점 (거래일)
                row["low_date"] = dates[low_i].strftime("%Y-%m-%d")
            for k, n in H.items():
                t = entry + n
                if t > last:
                    row[k] = None
                    row["sp" + k] = None
                    continue
                p1 = df[s].iloc[:t + 1].dropna()
                row[k] = round(float(p1.iloc[-1] / e_px - 1) * 100, 1) if not p1.empty else None
                row["sp" + k] = round(float(spx.iloc[t] / spx.iloc[entry] - 1) * 100, 1)
            picks.append(row)
    surv = {"pit": True, "src": "github.com/fja05680/sp500", "members": len(ever), "removed": len(removed),
            "removed_with_price": len(removed) - len(no_price), "no_price": len(no_price),
            "no_price_list": no_price[:400], "no_price_at_shock": miss_at,
            "picks_removed": sum(1 for p in picks if p["removed"])}
    out = {"shocks": [{"d": s["d"], "g": s["g"][:3], "dd": s["dd"]} for s in shocks], "picks": picks,
           "through": dates[-1].strftime("%Y-%m-%d"), "surv": surv}
    C.save("backtest10y.json", out)
    print("picks", len(picks), surv["no_price"], "no price")
    build_model(picks, surv=surv)


def build_model(picks, min_n=20, surv=None):
    """1년 내 회복 확률 표: 회복 가능성 점수(0~4) x 진입 시 회복 정도(60/70/80/90% 구간).
    코로나 기간(보유 기간이 2020-02~2020-12와 겹치는 경우)은 거의 모든 종목이 반등해 확률을
    부풀리므로 뺀다. 보정 없이 실제 건수 그대로 쓰고, 칸의 표본이 min_n건 미만이면
    같은 점수 전체의 실제 비율을 쓴다."""
    import pandas as pd
    x = pd.DataFrame(picks)
    x = x[x["full1y"]].copy()
    if "unk" in x:  # 사이트 기본값('자료 없음 통과' 꺼짐)과 같게: 자료가 없는 종목은 뺀다
        x = x[~x["unk"].fillna(False).astype(bool)].copy()
    sd = pd.to_datetime(x["shock"])
    cov = (sd + pd.DateOffset(months=14) >= "2020-02-01") & (sd <= "2020-12-31")
    n_all, n_cov = len(x), int(cov.sum())
    x = x[~cov].copy()
    fin = x.sec.isin(["금융", "부동산"])
    sdd = pd.concat([x.shock_dd.fillna(0), x.sec_med.fillna(0)], axis=1).min(axis=1)
    sdd[x.shock_dd.isna() & x.sec_med.isna()] = float("nan")
    x["score"] = (x.mom12 > 0).astype(int) + (fin | (x.de <= 100)).astype(int) + \
        (x.vol60 <= 30).astype(int) + (sdd <= -20).astype(int)
    # 사이트(app.js)와 같은 구간: <60, 60~70, 70~80, 80 이상
    x["rb"] = pd.cut(x.rec_entry, [-1e9, 60, 70, 80, 1e9], right=False, labels=False).fillna(0).astype(int)
    x["hit"] = x.hit1y.fillna(False).astype(bool)
    x["up"] = x["1y"] > 0

    def st(c):
        n = int(len(c))
        return {"n": n, "h": int(c.hit.sum()), "p": round(float(c.hit.mean()), 3) if n else None,
                "u": round(float(c.up.mean()), 3) if n else None,
                "r": round(float(c["1y"].median()), 1) if n else None}
    tab, grp, thr = {}, {}, {}
    for s in range(5):
        xs = x[x.score == s]
        grp[s] = st(xs)
        tab[s] = {}
        for b in range(4):
            c = xs[xs.rb == b]
            if len(c) >= min_n:
                tab[s][b] = dict(st(c), lv="cell")
            else:
                tab[s][b] = dict(grp[s], lv="score")
        thr[s] = st(x[x.score >= s])
    # 예상 고점 시기: 악재 저점 → 1년 안 최고가까지 걸린 거래일 분포 (점수별, 표본 20건 미만이면 전체)
    peak = {}
    if "peak_days_low" in x:
        allp = x.peak_days_low.dropna()
        for s in range(5):
            v = x[x.score == s].peak_days_low.dropna()
            lv = "score" if len(v) >= min_n else "all"
            v = v if len(v) >= min_n else allp
            if len(v):
                peak[s] = {"n": int(len(v)), "p25": int(v.quantile(.25)), "p50": int(v.median()), "p75": int(v.quantile(.75)), "lv": lv}
    yrs = sorted(x.shock.str[:4].unique())
    C.save("model.json", {"table": tab, "group": grp, "thr": thr, "peak": peak, "bins": [60, 70, 80, 90],
                          "n": int(len(x)), "n_all": n_all, "n_covid": n_cov, "min_n": min_n,
                          "years": f"{yrs[0]}~{yrs[-1]}", "base": round(float(x.hit.mean()), 3),
                          "desc": "코로나 기간 제외, 악재 2달 뒤 후보가 1년 안에 악재 전 최고가를 회복한 실제 비율",
                          "surv": {k: v for k, v in (surv or {}).items() if k in ("pit", "members", "removed", "removed_with_price", "no_price", "picks_removed")},
                          "unk_excluded": True})


if __name__ == "__main__":
    main()
