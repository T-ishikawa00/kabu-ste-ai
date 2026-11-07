# リスク制御と注文経路

## 実装の配置

注文系の判断は単一モジュールではなく、Controller・Router・Broker・APIに分かれています。

| 層 | 主なコード | 主な責務 |
|---|---|---|
| entry・exit条件 | [`controller.py`](../controller.py#L491) | 時刻、建玉数、注文間隔、entry可否、panic exit、TPSL確認、決済。 |
| 戦略選択 | [`strategy_router.py`](../strategy_router.py#L87) | Contextと推論結果を使う戦略分岐。Controllerから静的呼出しあり。 |
| 注文調整 | [`broker.py`](../broker.py#L60) | 注文可否、買い・空売り・決済・取消、建玉・約定同期。 |
| API通信 | [`kabus_api.py`](../kabus_api.py#L22) | 認証、板・建玉・注文照会、新規注文・取消等。 |

Controllerの注文処理は `self.brk` 経由でBrokerの注文メソッドに進みます。BrokerはAPI層を呼び出す実装を含みます。これがライブ動作の成功や安全性を証明するわけではありません。

## 実装済みと利用未確認の区別

Controllerには新規買い/空売り条件、保有上限、注文間隔、既存建玉回復、panic exit、TPSL判定、決済処理があります。Brokerにも注文前判定と約定・建玉同期があります。コードは保持していますが、損失限度・注文拒否・タイムアウト・重複注文などの実環境動作は確認していません。

[`risk_sizer.py`](../risk_sizer.py#L21)、[`entry_decider.py`](../entry_decider.py#L17)、[`exit_decider.py`](../exit_decider.py#L16) は独立ファイルとして存在します。root Python内の静的調査ではクラス名の利用を確認していないため、現行Controllerの制御として説明していません。

## 実行禁止の理由

Controllerの初期化にはAPI/Broker生成、モデル/Pickleロード、Notifier呼出しが含まれます。実際のWebSocket起動にはAPI認証、銘柄登録、ネットワーク接続へ進むコードがあります。注文関数は本物のBroker/APIへつながり得ます。`paper_trading`などの設定値だけでこれらの副作用が遮断されるとはみなせません。

## オフライン検証

安全な検証は、合成データ、Mock API/Broker、偽の建玉・約定を用いる隔離runnerに限定します。新規Mockは説明用の補助コードで、元のPPO推論・本番注文や過去の損益を再現しません。実注文・認証・口座・通知先・本物のモデルへの接続は一切行いません。
