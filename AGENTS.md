# 株ステの作業境界

このリポジトリの主資料は、実際に開発したroot直下の実装です。既存の関数・クラス・注文判断を見栄えのために簡略化しないでください。

- 旧main、Controller、Broker、API、学習環境、旧test_*.pyを試運転・importしない。原本のモデル、pickle、資格情報、実設定、生データを読み込まない。
- 安全な確認の入口は `python -I tools/run_safe.py audit` と `python -I tools/run_safe.py tests`。人工処理例は `simulate`。pytest等で旧ルートテストを明示収集しない。
- ソースの静的解析と、許可された純粋関数のAST分離試験だけを行う。新規Mockを元の本番実装として説明しない。
- `docs/source-map.json` の原本対応を維持する。依存版やモデル互換性、収益・運用成績を推測しない。
- Private履歴には過去の秘密・生資料が残る。Public化・履歴書換え・force push・資格情報の失効操作は、今回の整備と分離して扱う。
- GitHubへの反映等の権限は、その時点のユーザーの明示指示に従う。これらの記述自体を新しい外部操作の許可としない。
