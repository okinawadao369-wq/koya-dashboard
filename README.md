# koya-dashboard

KOYA AI v22 — 沖縄米軍マーケットインテリジェンス（スタッフ共有版）。
公開URL: https://familytreeoki.com/koya-dashboard/

## データ更新の担い手（2026-08-26 R-07/F21調査で確定・事実A/observed）

このリポジトリの `fred-data.json` と `index.html` は、**GitHub Actionsではなく**、
Cowork（Claude）のスケジュールタスク `koya-dashboard-fred-update`
（cron `0 9 * * *` ローカル時刻＝JST 09:06頃にジッター込みで実行）が、
GitHub Contents APIを直接叩いてコミットしている。

- タスク定義: `C:\Users\gpcok\OneDrive\Documents\Claude\Scheduled\koya-dashboard-fred-update\SKILL.md`
- 取得元: FRED API（11系列）+ Yahoo Finance（VIX/SP500/USD-JPY/WTI）
- 自己修復マージ: FRED実データが取れた日付は既存値を必ず上書きし、未来日の合成フォールバックは破棄する
- 取得失敗系列は前回値を保持しつつ `fetch_quality.results` に `"kept"` と記録する（沈黙して隠さない）

このリポジトリに `.github/workflows/` のデータ取得用ワークフローが存在しないのは仕様であり、
不具合ではない（唯一存在する `pages-build-deployment` はGitHub Pages自体のビルド用）。

### 既知の欠測

2026-08-17分のコミットが存在しない（原因未確認・証拠区分D）。この種の欠測が
黙って繰り返されないよう、既存の日次ヘルスチェックタスク（例:
`familytreeoki-daily-monitor`）に、このリポジトリの最新コミット時刻が
26時間を超えていたら警告する処理を追加することを提案している（2026-08-26時点で未実装）。

## 2026-08-26 R-07修理（F16〜F20）で修正した既知の欠陥

| ID | 内容 | 修正 |
|---|---|---|
| F16 | 取得失敗時も「更新成功」に見えていた（`#last-upd`が現在時刻に書き換わる、バナー非表示、コンソール無警告） | 失敗時は直前の成功時刻を保持し「(更新失敗 HH:MM)」を追記。バナー表示。`console.warn`を追加 |
| F17 | CPI前年比が固定インデックス参照のため13か月スパンで計算され3.5%と過大表示 | 「12か月前と同じ暦月」を日付で照合する方式に変更。該当月が無ければ「前年同月データなし」と表示 |
| F18 | `#ov-fxchg`の「前日比」がライブ値とFRED終値(最大5営業日差)の差分で、`fred-data.json`の`deltas`と矛盾 | `deltas`優先で再描画されるよう修正。`deltas`が無い場合は比較対象の日付を明示するラベルに変更 |
| F19 | `latest.DEXJPUS_source`が生成のたびに更新されず古い日付のまま固定されていた | 取得成功のたびに実際の取得時刻(JST)を動的に埋め込むよう`koya-dashboard-fred-update`タスクを修正 |
| F20 | `index.html`にFRED APIキーが平文で残存（`familytreeoki-dashboard`と共有の鍵） | `index.html`から完全削除。クライアント側からのFRED直接アクセス経路（CORSプロキシ経由フォールバック）自体を廃止 |

`koya-dashboard-evaluation`スキルのKOYA計器指数で再採点した結果は、採点台帳を参照。
