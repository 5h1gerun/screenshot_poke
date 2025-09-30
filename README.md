# OBS Screenshot / Template Tool

CustomTkinter 製のデスクトップ GUI アプリです。OBS WebSocket と連携し、以下を自動化します。
- 指定ソースのスクリーンショット取得とテンプレート照合
- 条件検知に応じた録画開始/停止（OBS 側）
- 配信向けの出力画像（は配信フォルダ）更新
- 画像の保存・タグ付け・ギャラリー表示・Discord 投稿
- 勝敗検知と戦績テキストの自動更新、結果の CSV 蓄積・統計表示

エントリーポイントは `combined_app.py` です。アプリ本体は `app/` 以下に分割されています。

## 対応環境
- OS: Windows / macOS / Linux
- Python: 3.10 以上
- OBS: WebSocket が有効（v4 プラグイン互換 API を優先、v5 互換パスも実装）
  - 旧 v4 プラグイン（obs-websocket）または v5 系でも一部機能は動作するようフォールバックを実装しています。

## 主要機能（概要）
- OBS 接続とシーン/ソース選択、スクショ取得
- ダブルバトル検知（テンプレート一致）→ 配信用画像更新＋素材保存
- 録画自動制御（開始/停止）→ 画像と録画ファイルの対応付け
- 勝敗/切断検知 → テキストソース更新（勝敗カウンタ）
- Discord Webhook への新着画像ポスト
- ギャラリー（サムネイル、検索、タグ編集）/ 統計（日次集計・勝率チャート）

---

## ディレクトリ構成と保存物
アプリは `.env` の `BASE_DIR` を起点に以下を作成/参照します。
- `handantmp/`（作業用）
  - テンプレート画像（例: `win.png`, `lose.png`, `disconnect.png`, `masu.png`, `masu1.png`, `mark.png`, `banme1.jpg`〜`banme4.jpg`）
  - 作業スクショ（`scene*.png`, `*cropped*.png` など、一時ファイル）
- `haisin/`（配信用）
  - 放送/配信用に常に上書きされる 1 枚（例: `haisinyou.png` または設定した拡張子）
- `koutiku/`（素材保管）
  - タイムスタンプ名の画像（例: `2025-09-25_21-06-17.png`）
  - 付随メタ: `_results.csv`（結果ログ）, `_tags.json`（タグ）, `_pairs.json`（画像と録画の対応）

拡張子やファイル名は環境変数で調整できます（後述）。

---

## セットアップ手順（ソースから）
1) 依存のインストール
   - `pip install -r requirements.txt`
2) `.env` の準備
   - `copy .env.example .env`（Windows）や `cp .env.example .env`（macOS/Linux）
   - 必要に応じて OBS 接続情報や `BASE_DIR` 等を修正
3) 起動
   - `python combined_app.py`

### パッケージ（EXE）での利用
- `dist/OBS-Screenshot-Tool.exe`（onefile）または `dist/OBS-Screenshot-Tool/OBS-Screenshot-Tool.exe`（onedir）を実行
- `.env` がある場合は「EXE と同じフォルダ」に置くと読み込まれます

---

## OBS 側の準備
- OBS WebSocket を有効化（ホスト/ポート/パスワードを `.env` または UI で設定）
- スクショ対象の「ソース名」を確認（例: `Capture1`）
- 戦績表示を使う場合は、OBS にテキストソース `sensekiText1` を作成しておくと反映されます（既定名）

---

## 画面構成と使い方（簡易）
- 左ペイン: 設定/操作
  - OBS 接続（Host/Port/Password、シーン/ソースの取得）
  - ベースディレクトリの参照/変更
  - 機能ごとの有効化（ダブルバトル検知/録画開始停止/勝敗検知/Discord 投稿/結果連携）
  - スレッドの開始/停止、ログ表示
- 右ペイン: タブ
  - Log: 処理ログ
  - Gallery: `koutiku` のサムネイル一覧、検索/タグ編集、エクスプローラで開く等
  - Stats: `_results.csv` を集計して日別の勝率チャート表示、期間/シーズンでの絞り込み、画像一覧連携

---

## テンプレート画像と検知ロジック（詳細）
本アプリは画面内の特定領域とテンプレート画像の「一致度」で状態を判定します。座標はコード内に既定値があり、主に 1920×1080 相当を想定しています。必要に応じてテンプレートの解像度を合わせてください。

1) ダブルバトル検知（`DoubleBattleThread`）
   - 毎ループで OBS からスクショを取得し、対象領域を切り出し（`screenshot_cropped.png`）
   - `handantmp/masu.png` を所定領域（`masu_area.png`）でテンプレ一致（しきい値 0.6）
   - 一致したら配信用画像（`haisin/haisinyou.<拡張子>`）を更新し、素材（`koutiku/CCYY-MM-DD_hh-mm-ss.<拡張子>`）を保存
   - 継続一致中は `banme1.jpg`〜`banme4.jpg` のテンプレ群と候補領域を走査し、抽出結果を `haisin/haisinsensyutu.png` に連結保存

2) 録画の開始/停止（`RkaisiTeisiThread`）
   - `masu1.png` 検知で録画開始、`mark.png` 検知で録画停止（いずれもテンプレ一致）
   - 録画状態は obs-websocket の複数 API を試行して確認（v4/v5 互換）
   - `RECORDINGS_DIR` を設定しておくと、録画セッションの時間帯と素材画像の時間帯を突き合わせ `_pairs.json` に対応付けを保存します

3) 勝敗/切断検知（`SyouhaiThread`）
   - `win.png` / `lose.png` / `disconnect.png` のテンプレ一致（領域はコード既定）
   - 検知するとカウンタ（勝/負/切断）を加算し、OBS のテキストソース（既定名 `sensekiText1`）に `Win/Lose/DC` を出力
   - 検知イベントは共有キューに流れ、後述の「結果連携」で新着画像と対にされます

4) 結果連携（`ResultAssociationThread`）
   - `koutiku` の新規画像を検出しつつ、勝敗イベント（タイムスタンプ付き）を FIFO で対応付け
   - 対応付け時に `_results.csv` へ追記し、`_tags.json` にタグ（win/lose/disconnect や `season:Sxx`）を付与
   - 「録画停止マーカ」が来て勝敗が見つからない場合は `win` を合成して割り当て（既定オフ、タイムアウト秒は環境変数で指定可能）

5) Discord 投稿（`DiscordWebhookThread`）
   - `koutiku` の新着画像（png/jpg/jpeg/webp）をポーリングし、Discord Webhook に `multipart/form-data` で POST
   - ファイルサイズが安定したタイミングで送る簡易デバウンスを実装

---

## ギャラリー/検索/タグ
- サムネイルは既定で最大 `GALLERY_MAX=100` 件、幅 `GALLERY_THUMB=240` px。
- Windows では同梱 DLL（WIC）による高速サムネイル生成を優先使用（失敗時は Pillow）。
- 検索はファイル名やタグを対象。`tag:XXX` 形式でタグ絞り込み、複数は空白/カンマ区切り。
- 各画像のタグはその場で編集可能（`_tags.json` に保存、重複は自動除去）。

---

## 環境変数（.env）
必要なものだけ設定すれば動作します。主な項目：

- OBS 接続
  - `OBS_HOST`（既定: `localhost`）
  - `OBS_PORT`（既定: `4444`）
  - `OBS_PASSWORD`
  - `OBS_SCENE`（空なら変更しない）
  - `OBS_SOURCE`（スクショ対象ソース名、例: `Capture1`）
- 作業フォルダ
  - `BASE_DIR`（`handantmp`/`haisin`/`koutiku` をぶら下げる起点、`.` でリポジトリ直下）
- 出力/命名
  - `OUTPUT_KOUTIKU_DIR`（既定: `koutiku`）
  - `OUTPUT_HAISIN_DIR`（既定: `haisin`）
  - `OUTPUT_HAISIN_BASENAME`（既定: `haisinyou`）
  - `OUTPUT_IMAGE_FORMAT`（`PNG`/`JPG`/`WEBP`、既定: `PNG`）
- UI
  - `APP_APPEARANCE`（`System`/`Light`/`Dark`、既定: `Dark`）
  - `APP_THEME`（`blue`/`dark-blue`/`green`）
  - `GALLERY_MAX`（既定: `100`）、`GALLERY_THUMB`（既定: `240`）
- スレッド有効化（既定値）
  - `ENABLE_DOUBLE`、`ENABLE_RKAISI`、`ENABLE_SYOUHAI`（true/false）
  - `ENABLE_DISCORD`、`DISCORD_WEBHOOK_URL`
- 録画/対応付け
  - `RECORDINGS_DIR`（OBS の録画出力フォルダ）
  - `RECORDINGS_EXTS`（拡張子リスト、例: `.mkv,.mp4`）
  - `RECORDINGS_MATCH_MARGIN_SEC`（マージン既定 20）
  - `ASSOC_TIME_TOLERANCE_SEC`（画像と勝敗イベントの許容差、既定 20）
- シーズン付与
  - `SEASON`（例: `13` 入力で `S13` に正規化。任意文字列も可）
- アップデート（任意）
  - `AUTO_UPDATE`、`UPDATE_FEED_URL`、`UPDATE_ASSET_PATTERN`（実装/利用は環境により限定的）

`.env.example` も参照してください。

---

## ビルドと配布（PyInstaller）
PowerShell スクリプト `scripts/build_exe.ps1` を使えます。
- 既定は onefile。誤検知対策や展開速度重視なら `-OneFile:$false`（onedir）を推奨
- VC++ ランタイム DLL を同梱し「Failed to load Python DLL」等の不整合を軽減
- 署名オプション（`-Sign`）あり（`signtool.exe` が必要）

例：
```
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -OneFile:$false -AddVersionInfo -NoUPX -Console:$false
```

出力場所：
- onefile: `dist/OBS-Screenshot-Tool.exe`
- onedir: `dist/OBS-Screenshot-Tool/OBS-Screenshot-Tool/OBS-Screenshot-Tool.exe`

---

## トラブルシュート（接続/実行時）
- OBS に接続できない：ホスト/ポート/パスワード、WebSocket の有効化を確認。ポート占有や FW も確認
- スクショが保存されない：ソース名（`OBS_SOURCE`）が実在するか、権限やプラグイン互換（v4/v5）を確認
- テキストが更新されない：OBS に `sensekiText1` があるか、名前/シーンが一致しているか確認
- Discord がエラーになる：Webhook URL/権限、HTTP 429/401 のログを確認。大きなファイルは避ける
- サムネイルが重い：Windows ならネイティブ DLL（`native/build/thumbnail_wic.dll`）が使われるか確認

---

## バージョン/ライセンス
- アプリ版数: `app/version.py` を参照（現在: 1.5.0）
- ライセンス: MIT（`LICENSE` 参照）

---

## 開発メモ（構成）
- エントリ: `combined_app.py`（`.env` を読み込んで `app.ui.app:main()` を起動）
- OBS I/F: `app/obs_client.py`（v4/v5 互換を考慮した安全ラッパ）
- スレッド: `app/threads/*.py`（ダブルバトル、録画制御、勝敗、Discord、結果連携）
- ユーティリティ: `app/utils/*.py`（画像処理、パス/保存、統計/CSV、ロギング、ネイティブサムネ）
- UI: `app/ui/app.py`（CustomTkinter によるメイン画面・ギャラリー・統計）

必要に応じて、座標やしきい値、テキストソース名などをコード側で調整してください。

