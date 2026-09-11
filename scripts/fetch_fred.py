#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
koya-dashboard  fred-data.json 生成スクリプト（GitHub Actions 実行版 v8.0）

Coworkスケジュールタスク v7.5 の STEP1 ロジックをそのまま移植したもの。
違いは次の3点のみ：

  1. 既存 fred-data.json は GitHub API ではなく「チェックアウト済みの作業ツリー」から読む
  2. 出力も API PUT ではなくファイル書き出し（コミットはワークフロー側の git が行う）
  3. FRED APIキーは環境変数 FRED_API_KEY（GitHub Secrets）から読む。PATは不要

スコアリング閾値・重み・トリガー文言・自己修復マージの挙動は v7.5 と同一。
index.html 側の MOCK ブロック更新と CACHE_KEY インクリメントは、このスクリプトでは
行わない（Claude 側の日次タスクが引き続き担当する）。
"""

import json
import os
import random
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
OUT_PATH = os.environ.get("OUT_PATH", "fred-data.json")

if not FRED_KEY:
    print("FATAL: 環境変数 FRED_API_KEY が設定されていません", file=sys.stderr)
    sys.exit(1)


def load_existing(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"WARN load_existing: {e}")
        return {"series": {}, "latest": {}, "prev_latest": {},
                "scores": {}, "koya_score": 50, "koya_signal": "HOLD"}


existing = load_existing(OUT_PATH)
prev_latest = dict(existing.get("latest", {}))
print(f"既存データ: {str(existing.get('updated', 'なし'))[:10]}")
print(f"既存系列数: {len(existing.get('series', {}))}")


def fetch_fred(sid, limit=14, retry=2):
    url = ("https://api.stlouisfed.org/fred/series/observations"
           f"?series_id={sid}&api_key={FRED_KEY}&limit={limit}"
           "&sort_order=desc&file_type=json")
    for attempt in range(retry + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"koya-v8-{sid}"})
            with urllib.request.urlopen(req, timeout=15) as r:
                data = json.loads(r.read())
            obs = [{"date": o["date"], "value": float(o["value"])}
                   for o in data.get("observations", [])
                   if o["value"] not in [".", "", None]]
            return obs if obs else None
        except Exception as e:
            if "429" in str(e) and attempt < retry:
                time.sleep(5 * (attempt + 1))
                continue
            print(f"  SKIP {sid}: {str(e)[:60]}")
            return None
    return None


def fetch_yahoo(symbol, name):
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 koya-v8"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        meta = d["chart"]["result"][0]["meta"]
        price = meta["regularMarketPrice"]
        prev = meta.get("chartPreviousClose")
        print(f"  {name} (Yahoo): {price} (前値 {prev})")
        return round(float(price), 2), (round(float(prev), 2) if prev else None)
    except Exception as e:
        print(f"  {name} Yahoo取得失敗: {e}")
        return None, None


print("\n=== Yahoo Finance データ取得 ===")
live_vix, prev_vix = fetch_yahoo("%5EVIX", "VIX")
live_sp500, prev_sp500 = fetch_yahoo("%5EGSPC", "SP500")
live_usdjpy, prev_usdjpy = fetch_yahoo("USDJPY=X", "USD/JPY(live)")
live_wti, prev_wti = fetch_yahoo("CL=F", "WTI原油")

fx_alert = None
if live_usdjpy is not None and prev_usdjpy is not None and prev_usdjpy > 0:
    fx_chg_pct = (live_usdjpy - prev_usdjpy) / prev_usdjpy * 100
    if abs(fx_chg_pct) >= 1.5:
        fx_alert = (f"⚠️ USD/JPY急変動検知: {prev_usdjpy}→{live_usdjpy} "
                    f"({fx_chg_pct:+.2f}%) — 介入・要人発言の可能性、要確認")
        print(f"\n{fx_alert}")

series_ids = [
    ("DEXJPUS", 14), ("FEDFUNDS", 14), ("UNRATE", 14),
    ("CPIAUCSL", 14), ("CPIFABSL", 14), ("AHETPI", 14),
    ("DGS10", 14), ("T10YIE", 14), ("UMCSENT", 14),
    ("GS2", 14), ("DCOILWTICO", 14),
]

series_data = dict(existing.get("series", {}))
current_latest = dict(existing.get("latest", {}))
fetch_results = {}

# 【v7.2で修正済みのバグ】実データが取れた日付は既存があっても必ず上書きし、
# 実データ最新日より未来の日付（過去の合成フォールバックの幻データ）は破棄する。
print("\n=== FRED データ取得（自己修復マージ） ===")
for sid, limit in series_ids:
    time.sleep(1.5)
    new_obs = fetch_fred(sid, limit)
    if new_obs is not None:
        newest_real_date = max(p["date"] for p in new_obs)
        by_date = {d["date"]: d for d in series_data.get(sid, [])
                   if d["date"] <= newest_real_date}
        added = 0
        overwritten = 0
        for pt in new_obs:
            if pt["date"] not in by_date:
                added += 1
            elif abs(by_date[pt["date"]]["value"] - pt["value"]) > 1e-9:
                overwritten += 1
            by_date[pt["date"]] = pt
        merged = sorted(by_date.values(), key=lambda x: x["date"], reverse=True)[:14]
        series_data[sid] = merged
        current_latest[sid] = (series_data[sid][0]["value"]
                               if series_data[sid] else current_latest.get(sid))
        status = f"OK {len(series_data[sid])}点(新{added}点/訂正{overwritten}点)"
        fetch_results[sid] = "ok"
    else:
        status = f"KEEP 既存保持({len(series_data.get(sid, []))}点)"
        fetch_results[sid] = "kept"
    print(f"  {sid}: {current_latest.get(sid, 'N/A')} {status}")

if live_usdjpy is not None:
    current_latest["DEXJPUS"] = live_usdjpy
    fetch_results["DEXJPUS"] = "ok(Yahoo-live)"
    _jst = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M JST")
    current_latest["DEXJPUS_source"] = f"Yahoo Finance RT {_jst}"
else:
    _prev_source = current_latest.get("DEXJPUS_source", "unknown")
    if not str(_prev_source).startswith("取得失敗"):
        current_latest["DEXJPUS_source"] = f"取得失敗（前回成功: {_prev_source}）"
if live_vix is not None:
    current_latest["VIX"] = live_vix
    fetch_results["VIX"] = "ok(Yahoo)"
if live_sp500 is not None:
    current_latest["SP500"] = live_sp500
    fetch_results["SP500"] = "ok(Yahoo)"
if live_wti is not None:
    current_latest["DCOILWTICO"] = live_wti
    fetch_results["DCOILWTICO"] = "ok(Yahoo)"

_monthly_sids = {"FEDFUNDS", "UNRATE", "CPIAUCSL", "CPIFABSL",
                 "AHETPI", "DGS10", "T10YIE", "UMCSENT", "GS2"}
_sid_spreads = {
    "DEXJPUS": 1.5, "FEDFUNDS": 0.03, "UNRATE": 0.15, "CPIAUCSL": 0.5,
    "CPIFABSL": 0.6, "AHETPI": 0.08, "DGS10": 0.05, "T10YIE": 0.03,
    "UMCSENT": 1.5, "GS2": 0.04, "DCOILWTICO": 1.5,
}


def _make_synthetic_series(base, n=14, spread=1.0, monthly=False):
    random.seed(int(float(base) * 100) % 9973)
    rows = []
    today_d = datetime.now(timezone.utc)
    for i in range(n - 1, -1, -1):
        if monthly:
            yr, mo = today_d.year, today_d.month - i
            while mo <= 0:
                mo += 12
                yr -= 1
            ds = f"{yr}-{mo:02d}-01"
        else:
            ds = (today_d - timedelta(days=i)).strftime("%Y-%m-%d")
        noise = (random.random() - 0.5) * spread * 2
        rows.append({"date": ds, "value": round(max(0.01, float(base) + noise), 3)})
    if rows:
        rows[-1]["value"] = round(float(base), 3)
    return rows


print("\n=== series 合成フォールバック（FRED取得が完全失敗した系列のみ） ===")
for _sid, _ in series_ids:
    if not series_data.get(_sid):
        _base = current_latest.get(_sid)
        if _base is not None:
            series_data[_sid] = _make_synthetic_series(
                _base, 14, _sid_spreads.get(_sid, 0.5), _sid in _monthly_sids)
            print(f"  {_sid}: 合成履武14点生成（暂定・次回実データで自動置換） base={_base}")
        else:
            print(f"  {_sid}: latest値もなし — スキップ")
    else:
        print(f"  {_sid}: FRED取得済み ({len(series_data[_sid])}点) — スキップ")

ok_count = sum(1 for v in fetch_results.values() if "ok" in v)
print(f"\n取得成功: {ok_count}系列（FRED+Yahoo合計）")

deltas = {}
all_tracked = list(series_ids) + [("VIX", 0), ("SP500", 0)]
for sid, _ in all_tracked:
    curr = current_latest.get(sid)
    prev = prev_latest.get(sid)
    if curr is not None and prev is not None and float(curr) != float(prev):
        deltas[sid] = round(float(curr) - float(prev), 4)
    else:
        deltas[sid] = None


def score_indicator(sid, value):
    if value is None:
        return 50
    v = float(value)
    if sid == "DEXJPUS":
        if 155 <= v <= 162:
            return 95
        if 150 <= v < 155 or 162 < v <= 165:
            return 80
        if 145 <= v < 150 or 165 < v <= 170:
            return 60
        return 35
    if sid == "FEDFUNDS":
        if 2.5 <= v <= 4.5:
            return 75
        if 4.5 < v <= 5.5:
            return 60
        return 50
    if sid == "UNRATE":
        if v < 4.0:
            return 100
        if v < 4.5:
            return 85
        if v < 5.0:
            return 65
        if v < 6.0:
            return 40
        return 20
    if sid == "UMCSENT":
        if v > 80:
            return 100
        if v > 65:
            return 80
        if v > 50:
            return 55
        if v > 40:
            return 35
        return 18
    if sid == "DCOILWTICO":
        if v < 70:
            return 100
        if v < 80:
            return 85
        if v < 90:
            return 70
        if v < 100:
            return 45
        return 20
    if sid == "DGS10":
        if 3.5 <= v <= 4.5:
            return 80
        if 4.5 < v <= 5.5:
            return 60
        if 2.5 <= v < 3.5:
            return 65
        return 45
    if sid == "T10YIE":
        if 2.0 <= v <= 2.5:
            return 92
        if 1.5 <= v < 2.0 or 2.5 < v <= 3.0:
            return 72
        if v > 3.0:
            return 35
        return 55
    if sid == "CPIAUCSL":
        if v < 310:
            return 90
        if v < 330:
            return 70
        if v < 350:
            return 50
        return 30
    if sid == "CPIFABSL":
        if v < 320:
            return 90
        if v < 340:
            return 72
        if v < 360:
            return 52
        return 30
    if sid == "AHETPI":
        if v > 33:
            return 100
        if v > 31:
            return 80
        if v > 29:
            return 60
        return 40
    if sid == "VIX":
        if v < 15:
            return 95
        if v < 20:
            return 75
        if v < 25:
            return 42
        if v < 35:
            return 25
        return 10
    if sid == "SP500":
        if v > 7500:
            return 80
        if v > 7000:
            return 65
        if v > 6000:
            return 55
        if v > 5000:
            return 45
        return 30
    if sid == "GS2":
        return 70
    return 50

scores = {}
for sid in [s for s, _ in series_ids] + ["VIX", "SP500"]:
    scores[sid] = score_indicator(sid, current_latest.get(sid))

alerts = {sid: ("green" if sc >= 70 else ("yellow" if sc >= 45 else "red"))
          for sid, sc in scores.items()}

dgs10 = current_latest.get("DGS10")
gs2 = current_latest.get("GS2")
spread = round(float(dgs10) - float(gs2), 3) if dgs10 and gs2 else None
spread_str = f"{spread:+.3f}%" if spread is not None else "N/A"

WEIGHTS = {
    "DEXJPUS": 0.25, "UMCSENT": 0.15, "DCOILWTICO": 0.13,
    "UNRATE": 0.10, "T10YIE": 0.09, "FEDFUNDS": 0.08,
    "VIX": 0.08, "CPIAUCSL": 0.06, "DGS10": 0.04,
    "AHETPI": 0.02,
}
koya_score = round(sum(scores.get(sid, 50) * w for sid, w in WEIGHTS.items()), 1)
koya_signal = "GO" if koya_score >= 68 else ("WATCH" if koya_score >= 45 else "HOLD")

prev_scores = dict(existing.get("scores", {}))
contributions = []
if prev_scores:
    contrib_list = []
    for sid, w in WEIGHTS.items():
        cur_s = scores.get(sid)
        prev_s = prev_scores.get(sid)
        if cur_s is not None and prev_s is not None and cur_s != prev_s:
            contrib_list.append((sid, w * (cur_s - prev_s)))
    total_abs = sum(abs(c) for _, c in contrib_list)
    contrib_list.sort(key=lambda x: abs(x[1]), reverse=True)
    for sid, c in contrib_list[:3]:
        pct = round(abs(c) / total_abs * 100, 1) if total_abs > 0 else 0
        contributions.append({
            "indicator": sid,
            "score_before": prev_scores.get(sid),
            "score_after": scores.get(sid),
            "koya_score_contribution": round(c, 2),
            "contribution_pct": pct,
        })

anomaly_flags = []
for sid, cur_s in scores.items():
    prev_s = prev_scores.get(sid)
    if prev_s is not None and abs(cur_s - prev_s) >= 10:
        sign = "+" if cur_s > prev_s else ""
        anomaly_flags.append(f"{sid}: {prev_s}→{cur_s}pt ({sign}{cur_s - prev_s}pt) 要確認")

fallback_indicators = [sid for sid, v in fetch_results.items() if v == "kept"]

triggers = []
msg_signal = {
    "GO": "積極展開フェーズ。高単価・新メニュー投入に最適タイミング。",
    "WATCH": "準備・監視フェーズ。現状維持しつつ差別化訴求を継続。",
    "HOLD": "慎重運営フェーズ。コスト管理強化・既存顧客リテンションを最優先。",
}
triggers.append({
    "level": "green" if koya_score >= 68 else ("yellow" if koya_score >= 45 else "red"),
    "indicator": f"koya総合スコア {koya_score:.0f}/100 [{koya_signal}]",
    "message": msg_signal[koya_signal],
})

if fx_alert:
    triggers.append({"level": "red", "indicator": "USD/JPY急変動（介入疑い）", "message": fx_alert})

if anomaly_flags:
    triggers.append({"level": "yellow", "indicator": "急変異常値フラグ",
                     "message": "; ".join(anomaly_flags)})

fx = current_latest.get("DEXJPUS")
wti = current_latest.get("DCOILWTICO")
ur = current_latest.get("UNRATE")
sent = current_latest.get("UMCSENT")
vix = current_latest.get("VIX")
sp = current_latest.get("SP500")

if fx:
    fv = float(fx)
    if fv >= 162:
        triggers.append({"level": "red", "indicator": f"USD/JPY ¥{fv:.3f}",
                         "message": "BOJ介入リスク域。ドル建て明示検討。"})
    elif fv >= 155:
        triggers.append({"level": "green", "indicator": f"USD/JPY ¥{fv:.3f}",
                         "message": "軍属購買力MAX水準。高単価メニュー投入好機。"})
    elif fv >= 148:
        triggers.append({"level": "yellow", "indicator": f"USD/JPY ¥{fv:.3f}",
                         "message": "標準購買力ゾーン。通常運営継続。"})
    else:
        triggers.append({"level": "red", "indicator": f"USD/JPY ¥{fv:.3f}",
                         "message": "円高進行。付加価値訴求に集中。"})
    d = deltas.get("DEXJPUS")
    if d and abs(d) >= 0.8:
        triggers.append({"level": "red", "indicator": "USD/JPY急変",
                         "message": f"前日比{'+' if d > 0 else ''}{d:.2f}円の急変動。価格設定・予約に注意。"})

if vix is not None:
    vv = float(vix)
    if vv >= 30:
        triggers.append({"level": "red", "indicator": f"VIX恐怖指数 {vv:.2f}",
                         "message": "市場危機ゾーン(>30)。本国SOFA家族が資産不安。慎重モードへ。"})
    elif vv >= 20:
        triggers.append({"level": "red", "indicator": f"VIX恐怖指数 {vv:.2f}",
                         "message": "市場警戒ゾーン突破(>20)。本国の節約意識上昇に注意。"})
    elif vv >= 15:
        triggers.append({"level": "yellow", "indicator": f"VIX恐怖指数 {vv:.2f}",
                         "message": "通常範囲内。市場安定・購買行動平常通り。"})
    else:
        triggers.append({"level": "green", "indicator": f"VIX恐怖指数 {vv:.2f}",
                         "message": "市場極めて安定。消費意欲MAX環境。"})

if sp is not None:
    sv500 = float(sp)
    d_sp = deltas.get("SP500")
    if d_sp and d_sp <= -200:
        triggers.append({"level": "red", "indicator": f"S&P500 {sv500:,.0f}",
                         "message": f"大幅下落({d_sp:+.0f}pt)。本国家族の資産効果消滅。消費慎重化リスク。"})
    elif d_sp and d_sp <= -100:
        triggers.append({"level": "yellow", "indicator": f"S&P500 {sv500:,.0f}",
                         "message": f"下落傾向({d_sp:+.0f}pt)。本国消費マインドに影響可能性。"})
    elif sv500 > 7500:
        triggers.append({"level": "green", "indicator": f"S&P500 {sv500:,.0f}",
                         "message": "高水準維持。本国資産効果継続。SOFA家族の消費余力↑。"})
    else:
        triggers.append({"level": "yellow", "indicator": f"S&P500 {sv500:,.0f}",
                         "message": "良好水準。本国資産効果継続。"})

if wti:
    wv = float(wti)
    if wv >= 95:
        triggers.append({"level": "red", "indicator": f"WTI原油 ${wv:.2f}",
                         "message": "高値。燃料・物流コスト上昇。価格転嫁を検討。"})
    elif wv >= 80:
        triggers.append({"level": "yellow", "indicator": f"WTI原油 ${wv:.2f}",
                         "message": "やや高め。生活費上昇分を価格説明に活用。"})
    else:
        triggers.append({"level": "green", "indicator": f"WTI原油 ${wv:.2f}",
                         "message": "安定。可処分所得への圧力なし。"})

if sent:
    sv = float(sent)
    if sv < 50:
        triggers.append({"level": "red", "indicator": f"消費者信頼感 {sv:.1f}",
                         "message": "低水準。本国比バリュー・安心感訴求を前面に。"})
    elif sv < 65:
        triggers.append({"level": "yellow", "indicator": f"消費者信頼感 {sv:.1f}",
                         "message": "慎重モード。「体験の価値」を丁寧に説明。"})
    else:
        triggers.append({"level": "green", "indicator": f"消費者信頼感 {sv:.1f}",
                         "message": "消費意欲良好。高単価オプション訴求に最適。"})

if ur and float(ur) >= 5.0:
    triggers.append({"level": "red", "indicator": f"失業率 {ur}%",
                     "message": "高水準。コスパ訴求を強化。"})

output = {
    "updated": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    "series": series_data,
    "latest": current_latest,
    "prev_latest": prev_latest,
    "deltas": deltas,
    "alerts": alerts,
    "scores": scores,
    "koya_score": koya_score,
    "koya_signal": koya_signal,
    "spread_dgs10_gs2": spread_str,
    "vix_note": f"VIX={vix} {'市場警戒ゾーン突破（>20）' if vix and float(vix) >= 20 else '正常範囲'}",
    "sp500_note": f"S&P500={sp}",
    "fx_intervention_alert": fx_alert,
    "contributions": contributions,
    "anomaly_flags": anomaly_flags,
    "fallback_indicators": fallback_indicators,
    "decision_triggers": triggers,
    "data_version": "v8.0-actions",
    "fetch_quality": {"ok": ok_count, "total": len(series_ids) + 2, "results": fetch_results},
}

with open(OUT_PATH, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
    f.write("\n")

print(f"\n✅ {OUT_PATH} を書き出しました")
print(f"🎯 koya経営スコア: {koya_score}/100 [{koya_signal}]")
print(f"   USD/JPY={fx}  VIX={vix}  S&P500={sp}  WTI={wti}")

summary = f"v8.0 [{koya_signal}] {koya_score:.0f}pt USD={fx} VIX={vix} SP={sp} {output['updated'][:10]}"
gh_out = os.environ.get("GITHUB_OUTPUT")
if gh_out:
    with open(gh_out, "a", encoding="utf-8") as f:
        f.write(f"summary={summary}\n")
        f.write(f"ok_count={ok_count}\n")
        f.write(f"koya_score={koya_score}\n")

# 取得が半分以上こけたら異常終了させ、Actions側で失敗として可視化する
if ok_count < 7:
    print(f"FATAL: 取得成功が{ok_count}系列のみ。データ取得系の障害の可能性。", file=sys.stderr)
    sys.exit(1)
