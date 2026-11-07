# セキュリティと公開前の課題

## 現在の扱い

この保存版はPrivateを維持する前提です。認証情報、Webhook、OAuth token、口座・個人情報の値は文書へ転載しません。最新コミットからのredactionと、過去のGit履歴からの除去は別の作業です。過去履歴に秘密情報が残る可能性を前提にしてください。

実注文に到達するBroker/API、認証、通知コードが存在します。安全検証はMock隔離入口だけで行い、実認証・API・実注文・外部通知を使いません。履歴中に有効な資格情報が含まれていた場合は、Public化前に利用停止・失効・再発行が必要です。値をチャットやログへ出さず、管理者の手順で対応してください。

## 最新状態とGit履歴

- `.gitignore` は今後の追跡を防ぐ補助であり、過去に追跡されたファイルを履歴から消しません。
- 最新版からのファイル除外・値の伏せ字は、履歴内の旧blobを消去しません。
- Privateを維持しながら、必要なら将来の公開専用履歴を別に再構築・精査する方法を検討します。今回、履歴の書換えやPublic化は行っていません。
- 認証情報が履歴内に残る可能性がある場合、履歴削除だけで十分とは限らず、必ず資格情報を失効させます。

## 安全な操作境界

元の `main.py`、`controller_ws.py`、`auto_login.py`、`test_notify.py` などは認証・外部I/Oに進む可能性があるため、確認目的で起動しないでください。実モデル、pickle、口座データ、生ログを読み込まず、安全runnerと合成データだけを利用します。`dashboard.html` はランタイムtelemetryや外部CDNを取得する可能性があるためブラウザーで開いていません。

## 公開を検討する前に

1. GitHubの機密データ削除手順を確認し、履歴全体を対象とする公開前レビューを実施する。
2. APIキー・Webhook・OAuth等の資格情報を所有者が失効・再発行し、関連サービス側で古い資格情報を停止する。
3. 個人情報、実取引データ、ログ、モデル、データセットの権利・再配布可否を確認する。
4. 公開する場合は、元Private履歴とは分離した公開用の履歴を再構築し、独立cloneで再検査する。
5. これらが完了するまで公開範囲をPrivateに保つ。

公式資料:

- [GitHub: Repositoryから機密データを削除する](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository)
- [GitHub: ファイルを無視する](https://docs.github.com/en/get-started/git-basics/ignoring-files)

手順の証跡と保全方法は [バックアップと出自](backup-and-provenance.md) を参照してください。
