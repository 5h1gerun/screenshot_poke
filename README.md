# OBS Screenshot Tool

CustomTkinter ベースの GUI アプリケーションです。OBS WebSocket と連携し、シーンやテンプレート画像の組み合わせ、テキスト更新、配信用画像の生成を行います。エントリーポイントは `combined_app.py` です。

## 動作環境
- Python 3.10 以上
- OBS（WebSocket 有効化）
- Windows / macOS / Linux（テンプレートや画面座標は環境依存）

## セットアップ
1) 依存関係のインストール

```
pip install -r requirements.txt
```

2) 環境変数の設定（任意だが推奨）
- `.env.example` を `.env` にコピーし、OBS のホスト/ポート/パスワードやベース作業ディレクトリ `BASE_DIR` を調整します。
- これらの値は起動後 GUI からも変更できます。秘密情報は `.env` に保存してください（Git では無視されます）。

## 使い方（起動）
GUI アプリを起動します。

```
python combined_app.py
```

起動後に確認する項目
- OBS 接続情報（host / port / password）
- ベースディレクトリ（`BASE_DIR`）。ここに `handantmp`、`haisin`、`koutiku` が存在（または自動生成）されます。
- テーマ設定（外観: System/Light/Dark、アクセント: blue/dark-blue/green）
- タブビュー: Log / Gallery
  - Gallery: `koutiku` 配下の画像をサムネイル一覧表示（最新順・最大100件）。
  - サムネイルをクリックすると拡大表示。Reload で更新、Auto Refresh で自動更新（既定ON）。

## ディレクトリ構成（主要）
- `handantmp/`: テンプレート画像や作業中のスクリーンショット
- `haisin/`: 配信用に出力される画像
- `koutiku/`: 後で再利用するための素材保管

`.gitignore` では `scene*.png`、`screenshot*.png`、`*cropped*.png` など一時生成物を除外します。`banme*.jpg` や `masu.png` など必要なテンプレートは保持してください。

## ビルド（実行ファイルの作成）
PowerShell スクリプト `scripts/build_exe.ps1` で PyInstaller ビルドを行います。初回は依存の取得・アイコン作成を含みます。

基本コマンド（onefile、既定設定）
```
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

誤検知（ウイルス誤判定）を減らしたい場合は onedir を推奨
```
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -OneFile:$false
```

主なオプション
- `-OneFile`（既定: `$true`）: `--onefile`/`--onedir` 切替。誤検知対策では onedir 推奨。
- `-NoUPX`（既定: `$true`）: UPX 圧縮を無効化。誤検知を防ぐため推奨。
- `-Console`（既定: `$false`）: コンソール表示の有無（デバッグ用途）。
- `-RuntimeTmp`（既定: `%LOCALAPPDATA%\PyInstallerCache`）: onefile 展開先。
- `-AddVersionInfo`（既定: `$true`）: Windows のバージョンリソースを埋め込み（CompanyName / ProductName 等）。
- `-CompanyName` `-ProductName` `-FileDescription`: バージョン情報の文字列を上書き。
- `-Sign`（既定: `$false`）: 署名を実行（Windows SDK の `signtool.exe` 必要）。
- `-PfxPath` `-PfxPassword` または `-CertThumbprint`: 署名証明書の指定。
- `-TimestampUrl`（既定: `http://timestamp.digicert.com`）: タイムスタンプサーバー。

出力先
- onefile: `dist/OBS-Screenshot-Tool.exe`
- onedir: `dist/OBS-Screenshot-Tool/OBS-Screenshot-Tool.exe`

コード署名の例（PFX を使用）
```
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -OneFile:$false -Sign -PfxPath "C:\path\code-signing.pfx" -PfxPassword "*****"
```

## 既知の誤検知（ウイルス判定）への対策
- onedir ビルドを利用（自己展開しないため誤検知が大幅に減ります）。
- コード署名（Authenticode）を実施。EV 証明書なら SmartScreen も通過しやすくなります。
- バージョン情報を埋め込む（CompanyName / ProductName など）。
- UPX は使わない（既定で無効）。
- 依存・PyInstaller を最新化。
- それでも出る場合は各 AV ベンダーへ誤検知申請（False Positive）を行うと解消されることがあります。

## トラブルシュート（ビルド）
- エラー: `FileNotFoundError: ... build\build\version_info.txt`
  - 理由: 相対パスが二重に解釈。現行スクリプトは絶対パスで渡すよう修正済み。
  - 対処: `build/OBS-Screenshot-Tool.spec` を削除して再ビルド、またはそのまま再ビルドで解消します。

- エラー: `ValueError: Failed to deserialize VSVersionInfo...`
  - 理由: `filevers` / `prodvers` が 4 要素の整数タプルになっていない。
  - 対処: 現行スクリプトは `(MAJOR, MINOR, PATCH, 0)` を自動生成します。再ビルドしてください。

## ライセンス
MIT License。詳細は `LICENSE` を参照してください。

