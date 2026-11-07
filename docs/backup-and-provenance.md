# 原本保全・出自・復元

基準main: `428f5d002c4a921d9ee8a5a3db8d983ac91ebed2` / tree: `2a579b6769a15c7cea1d6eddafb1970824496b3c`。

改修前にGitHubから新しい非shallow mirrorを隔離取得し、4参照・16コミットを確認しました。main、文書ブランチ、PR #1のhead/merge参照を保持し、タグは観測されませんでした。`fsck --full --strict`、全取得refを含むGit bundleの作成と検証、別の空ディレクトリへのbundle復元、復元後fsckとref一致を確認しました。

bundle SHA-256: `44ce86c08da301507350ee9d92522a88e5fe018b83d29cf3eb4428733767fd1b`

Git objectの全到達可能ID集合が前回監査と完全一致したため、33,777個のblobに対する既存のマスク検査記録も引き継いでいます。UTF-8テキスト32,759個をパターン検査、バイナリ・非UTF-8の1,018個は内容未走査です。新しいソース文字列検査と最新版検査は別途実施します。資格情報の有効性は試しません。

## 保全と改修の分離

原本mirror、bundle、復元確認copyは改修先から分離しました。新しいworktree用cloneをbundleから作り、元mainを親にする隔離ブランチで作業しています。保全原本のreset、clean、削除、上書きはしません。実装91ファイルは旧相対パスを維持し、元blobとSHA-256、最新hash、値除去位置を[source-map.json](source-map.json)へ記録しています。

保全先のACLはユーザー・SYSTEM・Administratorsへ限定しました。同一PC上のcopyであり、独立媒体のバックアップではありません。別媒体と暗号化状態は未確認です。完全なGit bundleとは取得したGit refからの履歴を指し、外部Release添付・LFS・他clone等の全資産を意味しません。基準treeにはsubmoduleもLFS pointerも検出されず、Release一覧・Actions run一覧は0でした。隠れたrefやGit外の全資料は未確認です。

## 復元手順

保管したbundleとSHA-256記録を用意し、新しい空の復元先を指定します。元作業先に上書きする操作は含めません。

```sh
git bundle verify <保存bundle>
git clone --mirror <保存bundle> <新規復元先.git>
git --git-dir=<新規復元先.git> fsck --full --strict
git --git-dir=<新規復元先.git> for-each-ref
```

各refと基準SHAを保管したmanifestと照合してください。復元した実設定・ソース・モデルを試運転して保全を確認する方法は使いません。生の保全資料は機密履歴を含むため、GitHub追加物や第三者への共有物にしません。個別のローカル保存先は今回の外部最終報告に記録します。

## 最新版から除外した資料

基準10,407ファイルのうち、実装91ファイルを保持し、10,316ファイルを最新版のindexから外しました。資格情報、実設定とbackup、実データ・取引ログ・生成report、モデル/正規化状態、cache、出所未確認のGUI参照画像が対象です。元assetと全履歴はbundle等に保全されています。除外理由別集計は[curation-summary.json](curation-summary.json)、全件の私的分類台帳はrepo外の保全領域にあります。

最新版を小さくしても、全履歴cloneの容量や過去の秘密は解消しません。Private履歴のままであり、公開用に無害化済みとはしていません。
