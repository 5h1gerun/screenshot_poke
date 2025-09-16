# OBS スクリーンショット／テンプレート ツール（GUI専用）

このリポジトリは、Tkinter ベースの GUI アプリケーション `combined_app.py` を提供します。OBS WebSocket 経由でシーンを取得し、テンプレートマッチングを実行して、OBS 向けの画像出力やテキスト更新を行います。

## 必要条件
- Python 3.10+
- OBS（WebSocket を有効化）
- Windows / macOS / Linux（画面座標やテンプレート素材は環境に依存）

## セットアップ
1. 依存関係のインストール:

```
pip install -r requirements.txt
```

2. 環境変数の設定（推奨）:
   - `.env.example` を `.env` にコピー
   - OBS のホスト／ポート／パスワードとベース作業ディレクトリ（`BASE_DIR`）に合わせて値を更新

これらの値はアプリ起動後に GUI からも設定できます。パスワードなどの秘密情報は Git に無視される `.env` に保存してください。

## 使い方
GUI アプリを起動:

```
python combined_app.py
```

起動後に確認:
- OBS 接続情報（host、port、password）
- ベースディレクトリ（`BASE_DIR`）: ここに `handantmp`、`haisin`、`koutiku` が存在（または生成）されます

## ディレクトリ構成
- `handantmp/`: テンプレート画像および作業中のスクリーンショット
- `haisin/`: 配信用の出力画像
- `koutiku/`: 後で再利用するための素材保存

`.gitignore` は、`scene*.png`、`screenshot*.png`、`*cropped*.png` などの一時的／生成画像をバージョン管理から除外します。`banme*.jpg` や `masu.png` など必要なテンプレートは保持してください。

## セキュリティ上の注意
- パスワードや個人情報はソースコードではなく環境変数に保存してください。
- テンプレートやスクリーンショット素材のライセンス／権利を公開前に確認してください。
- PR／Issue でログや画像を共有する際は、秘密情報が含まれないようにご注意ください。

## ライセンス
本プロジェクトは MIT ライセンスで提供します。詳細は同梱の `LICENSE` ファイルを参照してください。

