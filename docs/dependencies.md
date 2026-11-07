# 依存関係と再現性

## ソースから観測したimport

root Pythonには次の外部import名が現れます。これはソース上の参照から作った一覧であり、実際にインストール済みの環境、必要なバージョン、全スクリプトに必要な依存を示しません。

| 用途 | import例 |
|---|---|
| 強化学習 | `stable_baselines3`, `gymnasium`, `torch`, `cloudpickle` |
| 数値・特徴量 | `numpy`, `pandas`, `pandas_ta`, `optuna` |
| Web/API | `websocket`, `requests`, `urllib3`, `jquantsapi`, `yfinance` |
| 入出力・設定 | `yaml`, `csv`, `json`, `pickle`, `pathlib` |
| 補助/通知 | `googleapiclient`, `google_auth_oauthlib`, `pyautogui`, `matplotlib` |

原本のrootには `requirements.txt` と `pyproject.toml` がありません。今回のREADMEは依存のバージョン固定を追加していません。環境の組合せやPython対応版の整合は未検証です。

## 再現性の境界

- 依存インストールやモデル取得をこの資料では指示しません。誤った版の導入で本番入口を起動する危険があるためです。
- source-only保存版ではモデル・データ・生成ログの多くが除外されています。過去の学習runをそのまま再現できる前提はありません。
- 元スクリプトはroot直下のimportと相対パスに依存する可能性があります。構成整理で作業ディレクトリを変えるとパス解決が変わり得るため、既存配置を保っています。
- 安全runnerとMock検証は合成入力だけを使うため、元の外部サービスや実取引環境の再現ではありません。

再現可能な安全検証は [再現手順](reproduction.md) と [検証手順](verification.md) に従ってください。依存の解決・導入・モデル読込みを行う前に、対象の隔離とデータ・権利範囲を確認します。
