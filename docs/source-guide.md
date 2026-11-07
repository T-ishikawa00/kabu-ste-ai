# ソースガイド

## このガイドの読み方

「main静的経路」はroot直下ファイルのimportと、`main.py`からのスクリプト参照を静的にたどった分類です。関数が必ず呼ばれる、実口座で動く、当該機能が完成している、という意味ではありません。「独立」はコードがmain経路に静的接続していないことを表します。動的importや外部ランチャーはこの分類で確定できません。

## 主経路のソース

| 処理 | 実装 | 位置づけ |
|---|---|---|
| 統合起動 | [`main.py`](../main.py#L32) | WebSocket controllerを子プロセス起動する入口。 |
| WebSocketと銘柄イベント | [`controller_ws.py`](../controller_ws.py#L36) | Controllerを継承し、認証、接続、受信、銘柄別dispatchを実装。 |
| 売買制御 | [`controller.py`](../controller.py#L142) | 指標、推論、条件、注文、記録、通知の統合制御。 |
| API | [`kabus_api.py`](../kabus_api.py#L22) | 板・建玉・注文情報、注文・取消等の通信処理。 |
| Broker | [`broker.py`](../broker.py#L60) | 注文前の条件、売買・決済、建玉同期を実装。 |
| Router | [`strategy_router.py`](../strategy_router.py#L57) | Controllerから静的に生成・判定呼出しされる戦略分岐。 |
| 指標・共通処理 | [`utils_common.py`](../utils_common.py#L26) | 時刻、tick、丸め、共通ログ等の補助処理。 |
| 記録・通知 | [`data_collector.py`](../data_collector.py#L15), [`telemetry.py`](../telemetry.py#L20), [`notifier.py`](../notifier.py#L16) | 売買記録・状態保存・通知の実装。 |

## 学習・推論・検証の独立ソース

| 用途 | 実装 | 注意 |
|---|---|---|
| PPO環境 | [`step2_env.py`](../step2_env.py#L22), [`step2_env_universal.py`](../step2_env_universal.py#L16) | クラス名は両方`TradingEnv`。観測次元が異なる。 |
| 学習 | [`train_individual.py`](../train_individual.py#L121), [`train_universal.py`](../train_universal.py#L208), `step3_train*.py` | 学習処理はmainのライブ起動経路とは別。読み込みや保存を含む。 |
| モデル管理 | [`model_registry.py`](../model_registry.py#L13) | pickle/JSONを扱う。安全な検証入口で使わない。 |
| バックテスト | [`run_backtest.py`](../run_backtest.py#L51), [`run_backtest_hybrid.py`](../run_backtest_hybrid.py#L61), [`step4_backtest.py`](../step4_backtest.py#L14), [`step5_backtest.py`](../step5_backtest.py#L42), [`backtest_rules.py`](../backtest_rules.py#L42) | 個別スクリプト。実績・モデル互換・入力データを確認していない。 |

## 存在するがlive経路利用を確認していない実装

- [`features.py`](../features.py#L9) のentry/exit特徴ベクトル。
- [`entry_decider.py`](../entry_decider.py#L17) の `EntryDecider`。
- [`exit_decider.py`](../exit_decider.py#L16) の `ExitDecider`。
- [`risk_sizer.py`](../risk_sizer.py#L21) の `RiskSizer`。
- `regime_manager.py`、`symbol_gate.py` 等の補助コード。

静的参照が見つからなかったことは、動的参照・外部利用・過去の利用を否定しません。公開説明では、これらを現行の主要経路で利用していると書かないよう区別します。

## root配置を保つ理由

元コードはroot直下のimportと相対パスを前提にしたスクリプトが多く、データ・モデル・設定の参照も複数の場所に分散しています。初回の整理ではファイル移動をせず、既存の位置関係を保っています。構造変更時はimport・作業ディレクトリ・保存先・BAT参照・履歴対応を個別に再確認してください。

全root Python/BATの定義・参照台帳は [`source-map.json`](source-map.json) にあります。除外対象と保存版の出自は [`backup-and-provenance.md`](backup-and-provenance.md) にまとめる予定です。

## 全ファイルへの入口

領域別の[全91ファイル索引](module-index.md)と、定義位置・importを含む[module-catalog.json](module-catalog.json)から各実装へ到達できます。
