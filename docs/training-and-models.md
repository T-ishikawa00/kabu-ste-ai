# PPO学習・推論とモデル資産

本書の保存版treeは改修前の基準commitを指します。最新版ではモデル・正規化状態の実体を追跡対象から外し、元の学習・推論・モデル管理コードと参照を保持しています。保全済み原本と過去履歴には資産が残ります。

## 学習経路

リポジトリには複数のGymnasium環境とStable-Baselines3 PPO学習スクリプトがあります。`train_individual.py`、`train_universal.py`、`step3_train.py`、`step3_train_individual.py` は独立した入口で、保存・再開・評価の処理を含みます。学習データの完全性、報酬設計の有効性、モデル性能はこの静的資料からは判断できません。

## 観測次元の差

| ファイル | クラス | Box観測次元 | 観測 |
|---|---|---:|---|
| [`step2_env.py`](../step2_env.py#L22) | `TradingEnv` | 25 (`L178`) | 別系統の環境実装。 |
| [`step2_env_universal.py`](../step2_env_universal.py#L16) | `TradingEnv` | 18 (`L155`) | Universal側の環境実装。 |

両ファイルでクラス名が同じため、ファイル名・モデル名だけではロード先を確定できません。`train_individual.py` はUniversal側から `TradingEnv` をimportします。`run_backtest.py`、`run_backtest_hybrid.py`、`step5_backtest.py` もUniversal側を参照し、`step4_backtest.py` は別環境を参照します。

## 特徴ベクトルとの区別

[`features.py`](../features.py#L9) のentry/exitベクトル関数は独立補助コードです。entry補助ベクトルは9要素、exit補助ベクトルは10要素です。これらは環境の18/25観測空間やモデル入力次元と同じものだと扱えません。live controllerが同モジュールを呼ぶ静的な参照は確認できていません。特徴量仕様を統合する場合は、学習時と推論時の列・順序・正規化を別途照合してください。

## モデル世代と保存先

- `train_individual.py` / `step3_train*.py` にはv20系のモデル名参照があります。
- `train_universal.py` にはv23系の参照があります。
- 保存版treeには複数のmodel/runtime資産名が存在しますが、ここではバイナリをロードしていません。
- `ModelRegistry` は既定の保存先として `models` を参照しますが、保存版treeに同名ディレクトリはありません。別の `model/` と `runtime/` があることだけでは自動解決を証明しません。
- ControllerにはModelRegistryを通さない直接PPO/pickleのロード経路もあります。

したがって、次の対応表は未完成です。

| 確認項目 | 現時点 |
|---|---|
| 学習コードとモデルファイルの対応 | 未確認 |
| 18/25観測空間と各モデルの対応 | 未確認 |
| VecNormalize状態と方策の一致 | 未確認 |
| モデルの生成commit・学習データ・評価条件 | 未確認 |
| 推論時の依存バージョン互換性 | 未確認 |

## 安全な確認方法

このリポジトリの通常入口はモデルをロードする可能性があるため実行しません。安全な検証は新しく用意した隔離runnerのみで行います。runnerは合成データとMockを使う補助検証であり、PPOモデル推論を再現しません。モデル互換を検証するには、将来、権利・安全性を確認したモデルと固定環境の専用検証計画が必要です。
