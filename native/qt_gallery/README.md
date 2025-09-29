Qt ギャラリー（仮想化対応の高速リスト）

概要
- Qt Widgets の `QListView`（IconMode）を使った最小ギャラリーです。
- OS/フレームワーク側の仮想化により、数千枚でもスクロールは軽快です。

ビルド手順（Qt6 がインストール済みであること）
1. CMake 構成
   - Ninja 例:
     - `cmake -S . -B build -G "Ninja" -DCMAKE_PREFIX_PATH="C:/Qt/6.6.3/mingw_64"`
   - MinGW Makefiles 例:
     - `cmake -S . -B build -G "MinGW Makefiles" -DCMAKE_PREFIX_PATH="C:/Qt/6.6.3/mingw_64"`
2. ビルド
   - `cmake --build build --config Release`

起動
- `build/qt_gallery.exe <画像ディレクトリ>`

統合の方向性
- Python 側から `subprocess.Popen([qt_gallery.exe, koutiku_dir])` で起動する簡易連携が可能です。
- フル移行案では、サムネキャッシュ・タグ付け・動画ペアリング等を C++/Qt 側で再実装します。

