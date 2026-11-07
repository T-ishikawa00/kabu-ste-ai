# 検証結果 — 2026年11月7日

## 結果

| 検査 | 実測結果 | 保証の範囲 |
| --- | --- | --- |
| 元の実装保持 | 91ファイル、77バイト同一、14ファイルの17文字列のみ除去 | original pathを維持。機密・個人作業先以外の編集なし |
| Python構文・AST | 原実装87本が成功、文字列内容を除くAST構造一致 | 原本モジュールをimportせず解析 |
| JavaScript構文 | 既存dashboard inline scriptがnode --check成功 | HTML表示・fetch・外部CDNは起動していない |
| 安全テスト | 新規venv 2環境で各23テスト成功 | 指定tests/safeだけを収集 |
| 対象の既存純粋関数 | 7関数をfingerprint照合後にAST分離 | 型注釈の評価とmodule/class初期化をしない |
| 監視対象禁止呼出し | 通常テスト・補助例・auditで0件 | socket、子process、旧import、実設定・モデル読込み等 |
| 監視negative controls | 意図的6件が全て拒否 | localhost/外部形式、子process、資格情報、モデル、Controller import |
| 補助シミュレーション | 2環境と任意CWDで出力一致 | 新規の人工データ・Mockだけ。実運用再現ではない |

Python 3.12.3 / pip24.0の既存Ubuntu 24.04 / WSLを使用し、追加の第三者テスト依存は0件です。nodeの版は[evidence/validation-summary.json](evidence/validation-summary.json)へ記録しています。元のmigrateスクリプトにはdocstringのescapeに関するSyntaxWarningが残りますが、構文エラーではありません。元の処理を保つため文言以外のコード変更はしていません。

```sh
sh tools/verify_isolated.sh . <空の結果ディレクトリ>
```

隔離はuser/mount/network/PID名前空間とchrootで実施しました。最新版ソースと新規検証資材だけを渡し、原本.git・生資料・実認証を渡していません。環境変数を消去し、loはDOWN、IPv4/IPv6経路は空、/usrと子mountは読取専用です。ホストのネットワークや防火壁は変更していません。これは限定したOS隔離であり、ホスト全体の完全隔離とはしていません。

## 試験した処理

- `features._norm`、`MarketTemperature._scale01/compute`: 正規化、飽和、冷/熱側、同一境界、欠落値。
- `RiskSizer._features/size_for_strategy`: 8要素の順序、default、日次値の上下クランプ、入力不変、元のMINI_SWING最小値100。
- `TradingEnv._calc_realized_pnl`: long/short、建値、無ポジション、不正な建値境界。
- `StrategyRouter.decide`: 禁止条件の優先とSWING全条件、比較の境界。
- AST分離境界: Controller初期化等の非許可対象とpathを拒否。moduleトップレベルを実行しないこと、fingerprint改変時の拒否。
- 新規補助例: 不正・非有限入力、数量上限、損失上限、拒否時の状態不変、加重平均・long決済・short反転/決済、空イベント、決定性。

元の関数に新しい入力検証や仕様変更を足して合格させたものではありません。元の境界挙動をそのまま記録し、新規Mockの仕様とは分けています。元の関数の分離試験は、クラス束縛・コンストラクタ・実環境との互換性まで証明しません。

## 実行していないもの

Controller、Broker、API、旧main/BAT/旧test_*.py、学習・バックテストの全体実行、NumPy/Pandasの特徴ベクトル全体、PPO/モデル/VecNormalize、認証・GUI・メール・Discord・実注文・実口座・実データは未実施です。旧rootのtest_notify/test_runは自動収集していません。Windowsネイティブの動的実行も未検証です。

GitHubへの反映と独立cloneの最終SHA・全ファイル照合は、本repoの外にある最終報告で記録します。ソース・図の事実と人間による内容承認、技術テストと実売買安全性、Private反映とPublic化は分けて扱います。

証跡は[集計](evidence/validation-summary.json)、[静的監査](evidence/source-audit.json)、[テスト1](evidence/tests-one.txt)、[テスト2](evidence/tests-two.txt)、[呼出し監視](evidence/guard-controls.json)、[隔離](evidence/isolation.txt)を参照してください。
