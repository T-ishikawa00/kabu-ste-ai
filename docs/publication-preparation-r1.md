> R2追記：この文書はR1時点の記録です。素材・メールの追加本人承認と現状は[公開準備R2](PUBLICATION_PREPARATION_R2.md)を参照してください。

# 公開準備候補 R1

既存のT-ishikawa00/kabu-ste-aiを維持するA案です。リモートはPrivateのままです。元の全履歴・全refsは候補外のPrivate mirror/bundleで保全しています。候補では資格情報と秘密入り設定/backupを除き、第三者市場データ・未確認モデル・不要な大量生成物をPrivate保全へ分離しました。元の91実装と重要な設計資料を残し、後付けMockや別システムへ置換していません。

## 本人の確認

4資格情報群は[HUMAN_CONFIRMED_REVOKED](human-revocation-r1.json)。実接続・有効性テスト・再発行は行いません。本人の取引情報は無条件に機密扱いせず、[少量のBot記録](records/owner-trade-records/README.md)を残しています。市場データの再配布可否とは別です。

## 保全と履歴

前回の原本対応は[source-map.json](source-map.json)と[保全の記録](backup-and-provenance.md)に残しています。77ファイルの元blob一致と14ファイルの既存修正は、今回の候補でも再確認します。過去資料の元SHAやhashは元のPrivate原本を指し、本文を個人パス除去等で一般化した場合、一般化後のbytesと一致するという意味ではありません。全commit/ref対応表はrepo外の今回の納品にあります。

## 公開前の保留

作者/committerメールは本人判断前に変更しません。Packages・PR参照・GitHub cache・他cloneの処理は別途確認が必要です。GitHub Support対応が必要になる可能性があります。このローカル候補の作成だけでは、リモートの秘密が消えたことや公開可を意味しません。リモート更新/履歴更新/Public化は別の承認工程です。
