# 安全な閲覧・検証手順

主資料はrootに保持した実装です。まず[ソース案内](source-guide.md)、[構造](architecture.md)、[学習とモデル](training-and-models.md)を読み、GitHubのソース表示で参照してください。`main.py`、BAT、Controller等の初期化、旧学習・バックテスト・test_*.pyは実行しません。`dashboard.html`にも外部CDNとtelemetry取得があるため、HTMLとして起動せずソースとして閲覧します。

## 検証環境

新規検証ツールの依存はPython標準ライブラリだけです。Python 3.12系を対象とし、実測版と結果は[verification.md](verification.md)に記録します。旧研究依存のインストールやPPO/model読込みを要求しません。

```sh
python -I tools/run_safe.py audit
python -I tools/run_safe.py tests
python -I tools/run_safe.py simulate
```

`audit`は91実装ファイルのhashと構文・AST構造、相対リンクを検査します。Pythonの旧モジュールをimportしません。`tests`は `tests/safe/test_safe_*.py` だけを収集し、許可した7つの純粋関数のASTをfingerprint照合後に分離して試験します。旧クラスのコンストラクタやmoduleトップレベルは実行しません。

`simulate`は今回新規作成した人工イベントの補助的な構造説明です。主役の既存システムを置き換えるものではありません。固定のPolicyStubとMockBrokerを使い、PPO推論・実売買・当時の成績を再現しません。通信先やliveモードを指定する引数はありません。

## 通信・実認証・原本履歴を渡さない実測

既に利用可能なLinux/WSL、Python 3.12、venv、unshare、mount、chrootが必要です。これらを自動導入しません。

```sh
sh tools/verify_isolated.sh . <空の結果ディレクトリ>
```

91ファイルの最新版ソースと、新規tools/tests/docs/configだけをコピーします。.git、履歴、原本データ・モデル・実設定・認証ファイルは渡しません。環境変数を消去し、user/mount/network/PID名前空間とchrootで実行します。/usrと子mountは読取専用、loopbackはDOWN、IPv4/IPv6の経路を持たせません。

2つの新規venvで指定テストと補助例を実行し、出力を比較します。任意CWDからの起動も確認します。通常実行は監視対象禁止呼出し0件を要求し、selfcheckの6件の意図的拒否と区別します。ホストのネットワーク・防火壁設定は変更しません。

呼出し監視だけをOS全体の完全隔離とは扱いません。各テストは一度結果を出して終了し、常駐しません。停止が必要な場合はCtrl+Cです。一時検証領域と証跡は保全のため一括削除しません。

## 試験対象外

NumPy/Pandasを必要とする9/10次元ベクトル全体、18/25次元環境の学習、PPO・VecNormalizeの互換、証券API、メール・GUI、注文の実行、元バックテストの成績、実機での継続運用は検証していません。`pytest`用の自動対象もsafeディレクトリに限定しましたが、旧ファイルの明示実行や無差別importを安全な操作とはみなしません。
