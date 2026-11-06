# コード構成と処理の概観

この文書は、GitHubに残るPythonコードの静的調査に基づく**概要**です。実行試験・全期間の運用検証は実施していません。

## 主要コンポーネント

| コンポーネント | コード上で確認できる責務 |
| --- | --- |
| `main.py` | 起動前の確認、ログ設定、統合起動用のエントリーポイント |
| `controller.py` | 銘柄処理、指標・モデル判断の呼び出し、エントリー・決済・終了条件、取引記録 |
| `kabus_api.py` | 認証、板情報・建玉の取得、銘柄登録、注文送信・取消などのAPIラッパー |
| `broker.py` | 売買注文、建玉状態の照合、注文の重複抑制、決済処理 |
| `data_collector.py` | 板情報等の運用データの保存 |
| `model_registry.py` | モデルの読み込み・参照・予測と記録 |
| `strategy_router.py` | 市場やモデルの情報を踏まえた判断ルーティング |
| `entry_decider.py`, `exit_decider.py` | エントリー・決済方針と判断制御 |
| `risk_sizer.py` | リスクに基づくサイズ計算 |
| `telemetry.py`, `notifier.py` | 状態・数値の記録および通知 |

実装では複数のモジュールを組み合わせています。ここで示した分担は理解のための整理であり、各判断が常に一意のモジュールだけで完結することを意味しません。

## 学習と検証の主なファイル

- `step1_prepare_data.py` など：学習前のデータ準備。
- `step2_env.py`, `step2_env_universal.py`：Gymnasium系の強化学習環境。
- `step3_train.py`, `train_universal.py`：Stable-Baselines3のPPOを使用する学習コード。
- `run_backtest.py`, `run_backtest_hybrid.py`：過去データによる検証コード。
- `model/`, `runtime/`：学習済みモデルや関連する生成物。

ただし、保存されている学習済みモデルの性能や、データ期間・検証条件の妥当性は未検証です。

## 安全上の境界

このコードベースには、**模擬処理ではなく実際の注文APIにつながる機能**が存在します。

1. `auth_login.py`、`auto_login.py`、`check_gmail.py` には認証関連の処理があります。
2. `kabus_api.py` には `sendorder`、`cancelorder` などのAPI処理があります。
3. `broker.py` には `buy`、`sell`、`short_entry`、`cover` などの操作があります。
4. `step99_emergency_exit.py` も売買環境へ作用し得るため、実行せずに確認してください。

コードレビューのためにプログラムを起動する必要はありません。オフラインデモは、安全性を独立に検証できる実装が準備できるまで未提供とします。
