# OBS Screenshot/Template Tool (公開版)

このリポジトリは、OBS WebSocket からシーンのスクリーンショットを取得し、テンプレートマッチングを行ってファイル出力やOBSテキスト更新を行うツールです。GUI 版（Tkinter）と TUI 版（Textual）の2種類のフロントエンドが含まれます。

- GUI: `combined_app.py`
- TUI: `textual_app.py`

主な機能（クラス名の概略）

- DoubleBattleThread: 盤面などの複数テンプレートを検出して結合画像を `haisin/` に出力
- RkaisiTeisiThread: 条件に応じた録画開始/停止のトリガー
- SyouhaiThread: 勝敗/切断などを検知し OBS のテキストソースを更新

## 動作要件

- Python 3.10+
- OBS (WebSocket 有効)
  - OBS 28+ は WebSocket が同梱。適切に有効化してください。
- Tesseract OCR（実行ファイルのパスが必要）
- Windows/Mac/Linux で動作（スクリーン座標やテンプレートは環境依存です）

## セットアップ

1) 依存ライブラリのインストール

```
pip install -r requirements.txt
```

2) 環境変数の設定（推奨）

`.env.example` をコピーして `.env` を作成し、値を編集します（`.env` は `.gitignore` 済み）。

- `OBS_HOST`: 例 `localhost`
- `OBS_PORT`: 例 `4444`
- `OBS_PASSWORD`: OBS WebSocket のパスワード（空文字可）
- `TESSERACT_PATH`: 例 Windows: `C:\\Program Files\\Tesseract-OCR\\tesseract.exe`
- `BASE_DIR`: 作業用ディレクトリ（`handantmp`, `haisin`, `koutiku` をぶら下げるベース）。例 `.`

環境変数が未設定でも、アプリのUIから手動入力できます。公開リポジトリのため、パスワード等の秘匿情報をソース内に残さない設計に変更しています。

## 実行方法

- GUI 版（Tkinter）

```
python combined_app.py
```

- TUI 版（Textual）

```
python textual_app.py
```

起動後、以下を入力/確認してください。

- OBS 接続情報（Host/Port/Password）
- `Tesseract.exe` のパス
- ベースディレクトリ（`BASE_DIR`）。配下に `handantmp`, `haisin`, `koutiku` を使用します。

## ディレクトリ構成（抜粋）

- `handantmp/`: テンプレート画像やワーク中のスクリーンショット
- `haisin/`: 配信用の出力画像
- `koutiku/`: 生成物の保存先

`.gitignore` により、大量に変化する生成画像（例: `scene*.png`, `screenshot*.png`, `*cropped*.png` など）はコミット対象外にしています。必要なテンプレート（例: `banme*.jpg`, `masu.png` など）は残してください。

## セキュリティ / 公開上の注意

- パスワードや個人情報は `.env` や環境変数で管理し、ソースコードへ直書きしないよう変更済みです。
- 画像テンプレートやスクリーンショットには第三者の権利物が含まれる可能性があります。公開前にライセンス/権利をご確認ください。
- PRやIssueでログや画像を共有する際も、秘匿情報・個人情報にご注意ください。

## よくあるポイント

- Tesseract が見つからない: `TESSERACT_PATH` を正しく指定してください。
- 検出が不安定: 画面解像度/スケール、テンプレート画像、類似度閾値（コード内）を見直してください。
- OBS に接続できない: OBS の WebSocket を有効化し、Host/Port/Password を確認してください。

## ライセンス

このリポジトリのライセンスは、リポジトリオーナーが選択して追加してください（例: MIT/Apache-2.0 等）。
