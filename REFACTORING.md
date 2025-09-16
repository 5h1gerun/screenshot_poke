リファクタリング概要
====================

日付: 2025-09-16

概要
- これまで単一ファイルだった `combined_app.py` を `app/` 配下の小さなモジュール群へ分割しました。
- OBS 操作と画像ユーティリティを集約し、共通化しました。
- 実行エントリーポイントは既存通りで、`python combined_app.py` で起動できます。

新しい構成
- app/obs_client.py — `obs-websocket-py` のスレッドセーフな薄いラッパー
- app/utils/image.py — 画像のクロップ・テンプレートマッチのヘルパー
- app/utils/logging.py — UI向けのスレッド対応ロガー
- app/threads/double_battle.py — 旧 DoubleBattleThread のロジック
- app/threads/rkaisi_teisi.py — 旧 RkaisiTeisiThread のロジック
- app/threads/syouhai.py — 旧 SyouhaiThread のロジック
- app/ui/app.py — CustomTkinter の GUI（`App` と `main()`）

実行方法
- 変更前と同じ: `python combined_app.py`

補足
- 旧ファイルの一部に文字化けが含まれていました。新しいGUIでは英語ラベルに統一しています。
- 環境変数（`OBS_HOST`, `OBS_PORT`, `OBS_PASSWORD`, `BASE_DIR`）を読み込みます。
- 安全性・可読性の向上が目的で、機能の意図的な変更は行っていません。
