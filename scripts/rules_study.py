"""손실 줄이기 규칙 검증 (backtest10y.py 가 만든 picks 사용)

기본 표본: 사이트 기본값과 같음 = 회복 가능성 3점 이상, 코로나 기간 제외, 1년 보유가 끝난 거래.
기준값(문턱)은 앞 기간(악재 2021년 이전)에서 고르고, 뒤 기간(2022년 이후)에서 판정한다.
앞 기간의 3점 이상 표본이 적어서(약 30건) 문턱 고르기는 앞 기간의 '조건 통과 전체(점수 무관)'로 한다.
채택 기준: 뒤 기간에서 손실(평균·최악 최대하락 또는 -20% 넘게 잃은 비율)이 줄고, 중앙 수익률이 2%p 넘게 줄지 않을 것.

python scripts/rules_study.py  → docs/data/model.json 의 "rules" 를 다시 계산 (주가 받기 없음)
"""
import json
import os
import numpy as np
import pandas as pd

SPLIT = "2022-01-01"


def frame(picks):
    x = pd.DataFrame(picks)
    x = x[x["full1y"]].copy()
    if "px" not in x:
        return None
    x = x[x.px.apply(lambda p: bool(isinstance(p, list) and len(p) >= 200 and p[0]))].copy()
    sd = pd.to_datetime(x["shock"])
    cov = (sd + pd.DateOffset(months=14) >= "2020-02-01") & (sd <= "2020-12-31")
    x = x[~cov].copy()
    fin = x.sec.isin(["금융", "부동산"])
    x["fin"] = fin
    sdd = pd.concat([x.shock_dd.fillna(0), x.sec_med.fillna(0)], axis=1).min(axis=1)
    x["score"] = (x.mom12 > 0).astype(int) + (fin | (x.de <= 100)).astype(int) + \
        (x.vol60 <= 30).astype(int) + (sdd <= -20).astype(int)
    x["early"] = x.shock < SPLIT
    return x


def path(r):
    p = [v for v in r.px]
    for i in range(1, len(p)):  # 빈 날은 앞 값
        if p[i] is None:
            p[i] = p[i - 1]
    return np.array(p, dtype=float) / 1000.0


def bday(d0, d1):
    return int(np.busday_count(d0, d1))


def sim_exit(r, k=None):
    """k 거래일째 다음 날 종가에 판다(None 이면 1년 보유). (수익률%, 최대하락%, 최고가 회복 여부)"""
    p = path(r)
    end = len(p) - 1 if k is None else max(1, min(k, len(p) - 1))
    q = p[:end + 1]
    rec = bool((q[1:] >= r.pre_high / 1000.0).any())
    return (q[-1] - 1) * 100, (min(q.min(), 1.0) - 1) * 100, rec


def sim_stop(r, stop):
    p = path(r)
    hit = np.where(p[1:] <= 1 + stop / 100)[0]
    if not len(hit):
        return sim_exit(r) + (False, False)
    k = int(hit[0]) + 1
    ret, mdd, rec = sim_exit(r, k)
    full_ret, _, full_rec = sim_exit(r)
    return ret, mdd, rec, True, bool(full_rec or full_ret > 0)  # 손절 뒤 1년 안에 회복(또는 1년 뒤 플러스)했는지


def sim_staged(r, mode):
    """3번 나눠 사기. time: 신호일·1달 뒤·2달 뒤. dip: 신호일·-10% 추가 하락·-20% 추가 하락(안 오면 그 돈은 현금).
    수익률·최대하락은 계획한 전체 금액 기준(현금 포함)."""
    p = path(r)
    if mode == "time":
        buys = [0, min(21, len(p) - 1), min(42, len(p) - 1)]
    else:
        buys = [0]
        for lv in (0.9, 0.8):
            h = np.where(p <= lv)[0]
            buys.append(int(h[0]) if len(h) else None)
    val = np.zeros(len(p))
    for b in buys:
        if b is None:
            val += 1 / 3
            continue
        v = np.full(len(p), 1 / 3)
        v[b:] = p[b:] / p[b] / 3
        val += v
    rec = bool((p[1:] >= r.pre_high / 1000.0).any())
    return (val[-1] - 1) * 100, (min(val.min(), 1.0) - 1) * 100, rec


def stats(res):
    if not len(res):
        return {"n": 0}
    a = np.array([x[0] for x in res])
    m = np.array([x[1] for x in res])
    return {"n": int(len(a)), "mean": round(float(a.mean()), 1), "med": round(float(np.median(a)), 1),
            "rec": round(float(np.mean([x[2] for x in res])), 3), "mdd": round(float(m.mean()), 1),
            "worst": round(float(m.min()), 1), "l20": round(float((a < -20).mean()), 3)}


def first_post(r):
    """신호일 뒤 첫 실적 공시"""
    post = r.post if isinstance(r.post, list) else []
    return post[0] if post else None


def thesis_break(r, which):
    fp = first_post(r)
    if not fp:
        return None
    d, _, rev, op, _ = fp
    bad = (which in ("rev", "any") and rev is not None and rev < 0) or (which in ("op", "any") and op is not None and op < 0)
    return bday(r.px_dates[0], d) + 1 if bad else None


def dilution(r, thr=1.05):
    post = r.post if isinstance(r.post, list) else []
    for d, _, _, _, ratio in post:
        if ratio is not None and ratio >= thr:
            return bday(r.px_dates[0], d) + 1
    return None


def run_rule(x, fn):
    return stats([fn(r) for r in x.itertuples()])


def pick_thr(early_all, flag, grid, min_share=0.1):
    """앞 기간(점수 무관)에서: 걸린 종목의 중앙 수익률이 가장 나쁜 문턱 (최소 10%는 걸려야)"""
    best = None
    for t in grid:
        f = early_all[flag(early_all, t)]
        if len(f) < max(5, min_share * len(early_all)):
            continue
        med = float(np.median([sim_exit(r)[0] for r in f.itertuples()]))
        if best is None or med < best[1]:
            best = (t, med, len(f))
    return best


def study(picks):
    x = frame(picks)
    if x is None or not len(x):
        return None
    base = x[x.score >= 3]
    early_all, later, early = x[x.early], base[~base.early], base[base.early]
    hold = lambda r: sim_exit(r)
    B = {k: run_rule(v, hold) for k, v in (("early", early), ("later", later), ("full", base))}
    rows = []

    def add_filter(rid, name, flag, thr, note=""):
        out = {"id": rid, "name": name, "kind": "filter", "thr": thr, "note": note}
        for k, v in (("early", early), ("later", later), ("full", base)):
            out[k] = run_rule(v[~flag(v, thr)], hold)
            out[k + "_flag"] = run_rule(v[flag(v, thr)], hold)
        rows.append(out)

    def add_exit(rid, name, fn, note="", trig=None):
        out = {"id": rid, "name": name, "kind": "exit", "note": note}
        for k, v in (("early", early), ("later", later), ("full", base)):
            out[k] = run_rule(v, fn)
            if trig:
                out[k]["stopped"] = int(sum(1 for r in v.itertuples() if trig(r) is not None))
        rows.append(out)

    # 1) 회사 고유 악재: 업종 중간값보다 더 빠진 정도, 하루 단위 업종 대비 최악 하락
    f_rel = lambda v, t: v.rel_drop <= t
    t = pick_thr(early_all, f_rel, [-10, -15, -20, -25, -30])
    if t:
        add_filter("rel", f"회사 고유 악재 의심: 업종보다 {abs(t[0])}%p 넘게 더 빠짐", f_rel, t[0])
    f_idio = lambda v, t: v.idio1d.fillna(0) <= t
    t = pick_thr(early_all, f_idio, [-5, -8, -10, -15])
    if t:
        add_filter("idio", f"회사 고유 악재 의심: 하루에 업종보다 {abs(t[0])}%p 넘게 급락", f_idio, t[0])
    # 2) 첫 실적에서 가설 깨짐 → 다음 날 매도
    for w, nm in (("rev", "매출 감소"), ("op", "영업이익 감소"), ("any", "매출 또는 영업이익 감소")):
        def fn(r, w=w):
            return sim_exit(r, thesis_break(r, w))
        add_exit("tb_" + w, f"신호 뒤 첫 실적에서 {nm} → 다음 날 매도", fn,
                 "실적 공시(10-Q·10-K) 다음 날 기준 — 실제 실적 발표는 보통 며칠 더 빠름",
                 trig=lambda r, w=w: thesis_break(r, w))
    # 3) 부채: 부채비율·이자보상배율 (금융·부동산 제외), 신호 뒤 주식 수 5%↑ 증가 → 매도
    f_de = lambda v, t: (~v.fin) & (v.de.fillna(0) > t)
    t = pick_thr(early_all, f_de, [100, 150, 200])
    if t:
        add_filter("de", f"부채비율 {t[0]}% 초과", f_de, t[0])
    f_ic = lambda v, t: (~v.fin) & v.icov.notna() & (v.icov < t)
    t = pick_thr(early_all, f_ic, [3, 5, 8])
    if t:
        add_filter("icov", f"이자보상배율 {t[0]}배 미만", f_ic, t[0])
    add_exit("dil", "신호 뒤 주식 수 5% 넘게 증가(증자) → 다음 날 매도", lambda r: sim_exit(r, dilution(r)), trig=dilution)
    # 4) 시장 추세
    add_filter("spx200", "신호일에 S&P 500이 200일선 아래", lambda v, t: v.below200.fillna(False).astype(bool), None)
    # 5) 나눠 사기
    add_exit("st_time", "3번 나눠 사기 (신호일·1달 뒤·2달 뒤)", lambda r: sim_staged(r, "time"), "계획한 전체 금액 기준")
    add_exit("st_dip", "3번 나눠 사기 (신호일·추가 -10%·추가 -20%, 안 오면 현금)", lambda r: sim_staged(r, "dip"), "계획한 전체 금액 기준")
    # 6) 손절
    for s in (-15, -25, -35):
        out = {"id": f"stop{abs(s)}", "name": f"손절 {s}% (종가 기준)", "kind": "exit"}
        for k, v in (("early", early), ("later", later), ("full", base)):
            res = [sim_stop(r, s) for r in v.itertuples()]
            out[k] = stats(res)
            hit = [x for x in res if x[3]]
            out[k]["stopped"] = len(hit)
            out[k]["stopped_then_rec"] = sum(1 for x in hit if x[4])
        rows.append(out)

    bl = B["later"]
    for r in rows:
        lt = r["later"]
        if not lt.get("n"):
            r["adopt"], r["why"] = False, "표본 없음"
            continue
        better = (lt["worst"] > bl["worst"] + 0.5) or (lt["mdd"] > bl["mdd"] + 0.5) or (lt["l20"] < bl["l20"] - 0.005)
        keep = lt["med"] >= bl["med"] - 2
        r["adopt"] = bool(better and keep and lt["n"] >= 20)
        r["why"] = ("손실 줄고 중앙 수익률 유지" if r["adopt"] else
                    "손실이 안 줄어듦" if not better else
                    f"중앙 수익률 {lt['med'] - bl['med']:+.1f}%p" if not keep else "표본 부족")
    return {"split": SPLIT, "base": B, "rows": rows, "eps_cut": "과거 EPS 전망 기록이 없어 검증 못 함 (사이트는 2026년부터 기록)",
            "sample": "회복 가능성 3점 이상·코로나 제외·1년 보유 완료. 문턱은 2021년 이전(점수 무관)에서 고르고 2022년 이후로 판정"}


if __name__ == "__main__":
    here = os.path.dirname(__file__)
    data = os.path.join(here, "..", "docs", "data")
    picks = json.load(open(os.path.join(data, "backtest10y.json"), encoding="utf-8"))["picks"]
    res = study(picks)
    print(json.dumps(res, ensure_ascii=False, indent=1))
