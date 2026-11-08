# セキュリティと公開前の課題

## 現在の扱い

GitHub上の原本は現在Privateで、公開準備候補だけを隔離ローカルで作成しています。既存リポジトリを維持するA案に基づく候補であり、push・Public化は未実施です。認証情報、Webhook、OAuth token、口座・個人情報の値は文書へ転載しません。最新コミットからのredactionと、過去のGit履歴からの除去は別の作業です。元Private履歴には秘密情報が残るため、保全原本を公開対象へ混入させないでください。候補では全取得refsを対象に資格情報・秘密入り設定/backupを除外し、commit対応表を作成します。

実注文に到達するBroker/API、認証、通知コードが存在します。安全検証はMock隔離入口だけで行い、実認証・API・実注文・外部通知を使いません。証券API・取引用パスワード、Discord Webhook、J-Quants認証情報、Google OAuth関連情報は、本人確認により HUMAN_CONFIRMED_REVOKED です。これは本人の確認記録で、Codexによる有効性テストではありません。失効済みでも不要な認証情報は候補の履歴から除外します。値をチャットやログへ出しません。

## 最新状態とGit履歴

- `.gitignore` は今後の追跡を防ぐ補助であり、過去に追跡されたファイルを履歴から消しません。
- 最新版からのファイル除外・値の伏せ字は、履歴内の旧blobを消去しません。
- Privateを維持しながら、必要なら将来の公開専用履歴を別に再構築・精査する方法を検討します。前回整備では履歴の書換えを行いませんでした。今回の候補では隔離コピー内だけを再構成し、リモート履歴・branch・PR・tagは変更していません。
- 認証情報が履歴内に残る可能性がある場合、履歴削除と失効は別です。今回の4群の失効は本人確認済みとして扱い、実接続・再発行を行いません。

## 安全な操作境界

元の `main.py`、`controller_ws.py`、`auto_login.py`、`test_notify.py` などは認証・外部I/Oに進む可能性があるため、確認目的で起動しないでください。実モデル、pickle、口座データ、生ログを読み込まず、安全runnerと合成データだけを利用します。`dashboard.html` はランタイムtelemetryや外部CDNを取得する可能性があるためブラウザーで開いていません。

## 公開を検討する前に

1. GitHubの機密データ削除手順を確認し、履歴全体を対象とする公開前レビューを実施する。
2. APIキー・Webhook・OAuth等の資格情報を所有者が失効・再発行し、関連サービス側で古い資格情報を停止する。
3. 個人情報、実取引データ、ログ、モデル、データセットの権利・再配布可否を確認する。
4. 公開する場合は、既存repo維持のA案として、秘密・未許諾データを除いた候補履歴を独立cloneで再検査する。
5. これらが完了するまで公開範囲をPrivateに保つ。

公式資料:

- [GitHub: Repositoryから機密データを削除する](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [GitHub: ファイルを無視する](https://docs.github.com/en/get-started/git-basics/ignoring-files)

手順の証跡と保全方法は [バックアップと出自](backup-and-provenance.md) を参照してください。
