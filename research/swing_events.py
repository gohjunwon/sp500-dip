"""스윙 연구용: S&P 500 실적 발표 이벤트별 특징과 이후 수익률 (GitHub Actions에서 실행)
결과: research/swing_events.json.gz
"""
import gzip
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import collect as C  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "swing_events.json.gz")


def earnings(sym):
    import yfinance as yf
    for i in range(3):
        try:
            ed = yf.Ticker(sym.replace(".", "-")).get_earnings_dates(limit=60)
            if ed is None or ed.empty:
                return sym, []
            out = []
            for ts, row in ed.iterrows():
                ts = ts.tz_convert("America/New_York")
                rep, est, sur = row.get("Reported EPS"), row.get("EPS Estimate"), row.get("Surprise(%)")
                if rep != rep:  # 아직 발표 전
                    continue
                out.append([ts.strftime("%Y-%m-%d"), ts.hour, None if est != est else float(est),
                            float(rep), None if sur != sur else float(sur)])
            return sym, out
        except Exception as e:
            time.sleep(3 * (i + 1))
            err = e
    print("earn fail", sym, err)
    return sym, []


def main():
    import numpy as np
    import pandas as pd
    import yfinance as yf
    meta = C.constituents()
    syms = list(meta)
    ymap = {s: s.replace(".", "-") for s in syms}
    raw = yf.download(list(ymap.values()) + ["^GSPC"], start="2015-06-01", auto_adjust=True,
                      progress=False, threads=True)
    O, H, L, Cl, V = (raw[k] for k in ["Open", "High", "Low", "Close", "Volume"])
    spx = Cl["^GSPC"]
    spx200 = spx.rolling(200).mean()
    dates = Cl.index
    print("prices", Cl.shape)

    with ThreadPoolExecutor(6) as ex:
        earn = dict(ex.map(earnings, syms))
    print("earnings events", sum(len(v) for v in earn.values()))

    # SEC 분기 매출 (발표된 분기의 1년 전 대비 성장률)
    rev = {}
    for s in syms:
        cik = meta[s].get("cik")
        if not cik:
            continue
        try:
            facts = json.loads(C.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"))
            rev[s] = C.quarterly_series(facts, C.REV_TAGS)
        except Exception as e:
            print("sec fail", s, e)
        time.sleep(0.12)

    rows = []
    for s in syms:
        y = ymap[s]
        if y not in Cl.columns:
            continue
        c, o, h, lo, v = Cl[y], O[y], H[y], L[y], V[y]
        rq = rev.get(s, {})
        rq_keys = sorted(rq)
        for d, hour, est, rep, sur in earn.get(s, []):
            dt = pd.Timestamp(d)
            # 장 시작 전(정오 이전) 발표면 그날, 장 마감 후 발표면 다음 거래일이 반응일
            i0 = int(dates.searchsorted(dt + (pd.Timedelta(days=1) if hour >= 12 else pd.Timedelta(0))))
            if i0 < 252 or i0 + 1 >= len(dates):
                continue
            if np.isnan(c.iloc[i0]) or np.isnan(c.iloc[i0 - 1]) or np.isnan(o.iloc[i0]):
                continue
            pc, c0 = c.iloc[i0 - 1], c.iloc[i0]
            rng = h.iloc[i0] - lo.iloc[i0]
            vol20 = v.iloc[i0 - 20:i0].mean()
            ma50 = c.iloc[i0 - 50:i0].mean()
            # 매출 성장: 발표일 전 100일 안에 끝난 분기 vs 1년 전 분기
            ry = None
            qe = [k for k in rq_keys if k <= d and (dt - pd.Timestamp(k)).days <= 100]
            if qe:
                cur = qe[-1]
                prev = [k for k in rq_keys if 330 < (pd.Timestamp(cur) - pd.Timestamp(k)).days < 400]
                if prev and rq[prev[-1]] and rq[prev[-1]] > 0:
                    ry = round((rq[cur] / rq[prev[-1]] - 1) * 100, 1)
            ev = {"s": s, "d": dates[i0].strftime("%Y-%m-%d"), "sec": meta[s]["s"],
                  "sur": sur, "gap": round(float(o.iloc[i0] / pc - 1) * 100, 2),
                  "r0": round(float(c0 / pc - 1) * 100, 2),
                  "clv": round(float((c0 - lo.iloc[i0]) / rng), 2) if rng > 0 else None,
                  "vr": round(float(v.iloc[i0] / vol20), 2) if vol20 > 0 else None,
                  "ma50": round(float(pc / ma50 - 1) * 100, 1),
                  "hi52": round(float(c0 / c.iloc[i0 - 252:i0 + 1].max() - 1) * 100, 1),
                  "pre20": round(float(pc / c.iloc[i0 - 21] - 1) * 100, 1),
                  "mkt": bool(spx.iloc[i0] > spx200.iloc[i0]), "rev": ry}
            for hz in (5, 10, 20, 40):
                j = i0 + hz
                if j < len(dates) and not np.isnan(c.iloc[j]):
                    ev[f"f{hz}"] = round(float(c.iloc[j] / c0 - 1) * 100, 2)
                    ev[f"m{hz}"] = round(float(spx.iloc[j] / spx.iloc[i0] - 1) * 100, 2)
                    ev[f"dd{hz}"] = round(float(lo.iloc[i0 + 1:j + 1].min() / c0 - 1) * 100, 2)
            rows.append(ev)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(rows, f, separators=(",", ":"))
    print("events", len(rows))


if __name__ == "__main__":
    main()
