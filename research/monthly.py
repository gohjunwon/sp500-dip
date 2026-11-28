"""악재 낙폭 검색기 10년 후보의 진입 후 1~12개월 월별 수익률 (코로나 제외/포함)"""
import json, pandas as pd, yfinance as yf
P = json.load(open("docs/data/backtest10y.json"))["picks"]
x = pd.DataFrame(P); x = x[x.full1y].copy()
fin = x.sec.isin(["금융", "부동산"])
sdd = pd.concat([x.shock_dd.fillna(0), x.sec_med.fillna(0)], axis=1).min(axis=1)
x["score"] = (x.mom12 > 0).astype(int) + (fin | (x.de <= 100)).astype(int) + (x.vol60 <= 30).astype(int) + (sdd <= -20).astype(int)
sd = pd.to_datetime(x.shock)
x["covid"] = (sd + pd.DateOffset(months=14) >= "2020-02-01") & (sd <= "2020-12-31")
syms = sorted(set(x.s)) + ["SPY"]
df = yf.download([s.replace(".", "-") for s in syms], start="2015-06-01", auto_adjust=False, progress=False, threads=True)["Close"]
df.columns = [c.replace("-", ".") for c in df.columns]
dates = df.index
rows = []
for _, r in x.iterrows():
    d = pd.Timestamp(r.shock)
    e = int((dates <= d + pd.Timedelta(days=61)).sum()) - 1
    s = df[r.s]
    if pd.isna(s.iloc[e]):
        continue
    m = {}
    for k in range(1, 13):
        t = min(e + 21 * k, len(dates) - 1)
        p0, p1 = s.iloc[e], s.iloc[:t + 1].dropna().iloc[-1]
        m[k] = (p1 / p0 - 1) * 100
        m["sp%d" % k] = (df.SPY.iloc[t] / df.SPY.iloc[e] - 1) * 100
    rows.append(dict(s=r.s, shock=r.shock, score=int(r.score), covid=bool(r.covid), y1=r["1y"], **m))
out = pd.DataFrame(rows)
res = {}
for name, g0 in [("excovid", out[~out.covid]), ("all", out)]:
    res[name] = {}
    for th in [0, 2, 3, 4]:
        g = g0[g0.score >= th]
        mm = []
        for k in range(1, 13):
            c = g[k]; pc = g[k - 1] if k > 1 else pd.Series(0.0, index=g.index)
            inc = ((1 + c / 100) / (1 + pc / 100) - 1) * 100  # 그 달만의 수익률
            mm.append(dict(m=k, n=len(g), cum_mean=round(c.mean(), 1), cum_med=round(c.median(), 1),
                           win=round((c > 0).mean() * 100), month_mean=round(inc.mean(), 1),
                           month_med=round(inc.median(), 1), month_win=round((inc > 0).mean() * 100),
                           sp_cum=round(g["sp%d" % k].mean(), 1)))
        res[name][th] = mm
res["check_1y_corr"] = round(float(out[12].corr(out.y1)), 3)
json.dump(res, open("research/monthly.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps(res["excovid"][3], ensure_ascii=False))
