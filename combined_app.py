import os
import time
import cv2
import numpy as np
import base64
import threading
import datetime
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import customtkinter as ctk

"""
combined_app.py

OCR 関連の残骸を削除し、保存ファイル名を「日付のみ」に統一。
"""
from obswebsocket import obsws, requests
from tkinter import ttk

# ---------------------------------------------------
# ヘルパー: ログ出力用
# ---------------------------------------------------

def thread_safe_log(func):
    """
    ログ出力をスレッドセーフに呼び出すためのデコレータ。
    - self.logger があればそれを利用（Textual等の外部コールバック想定）
    - なければ Tk の Text へ after で追記
    - さらに何も無ければ標準出力へ
    """
    def wrapper(self, message):
        # Textual などから渡されるコールバックが優先
        logger_cb = getattr(self, "logger", None)
        if callable(logger_cb):
            try:
                logger_cb(message)
            except Exception:
                # フォールバックして標準出力
                print(message)
            return

        # Tkinter のテキストウィジェットがある場合
        if getattr(self, "log_text", None):
            self.log_text.after(0, lambda: func(self, message))
            return

        # どちらも無い場合はコンソール出力
        print(message)
    return wrapper

# ---------------------------------------------------
# double_battle.py 相当
# ---------------------------------------------------
class DoubleBattleThread(threading.Thread):
    def __init__(self, ws, ws_lock, base_dir, log_text=None, logger=None):
        super().__init__()
        self.ws = ws
        self.ws_lock = ws_lock  # 追加: OBS呼び出し時のロック
        self.base_dir = base_dir
        self.screenshot_dir = os.path.join(base_dir, 'handantmp')
        self.haisin_dir = os.path.join(base_dir, 'haisin')
        self.hozon_dir = os.path.join(base_dir, 'koutiku')
        self.stop_flag = False
        self.log_text = log_text
        self.logger = logger

        self.scene_image_path = os.path.join(self.screenshot_dir, 'scene.png')
        self.masu_image_path = os.path.join(self.screenshot_dir, 'masu.png')
        self.combined_sorted_color_image_path_final = os.path.join(self.haisin_dir, 'haisinsensyutu.png')

        reference_image_files = ['banme1.jpg', 'banme2.jpg', 'banme3.jpg', 'banme4.jpg']
        self.reference_image_paths = [os.path.join(self.screenshot_dir, f) for f in reference_image_files]

        self.masu_coords = [(1541, 229), (1651, 229), (1651, 843), (1541, 843)]
        self.screenshot_coords = [(1221, 150), (1655, 150), (1655, 850), (1221, 850)]

        os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'

    def run(self):
        self.log("[DoubleBattle] スレッド開始")
        try:
            while not self.stop_flag:
                self.main_loop()
                time.sleep(2)
        except Exception as e:
            self.log(f"[DoubleBattle] エラー: {e}")
        finally:
            self.log("[DoubleBattle] スレッド終了")

    def stop(self):
        self.stop_flag = True

    @thread_safe_log
    def log(self, message):
        """ログ出力(テキストエリアに追記)"""
        print(message)
        if self.log_text:
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)

    def main_loop(self):
        # 1. シーンキャプチャ取得
        while True:
            if self.stop_flag:
                return
            self.takeScreenshot("Capture1", self.scene_image_path)
            if os.path.exists(self.scene_image_path):
                break
            time.sleep(1)

        # 2. スクリーンショット切り取り
        scene_cropped_img = self.crop_image_using_coords(self.scene_image_path, self.screenshot_coords)
        cropped_image_path = os.path.join(self.screenshot_dir, 'screenshot_cropped.png')
        cv2.imwrite(cropped_image_path, scene_cropped_img)
        self.log("[DoubleBattle] screenshot_cropped.png 保存")

        # 3. テンプレートマッチング
        masu_img = cv2.imread(self.masu_image_path)
        if masu_img is None:
            raise FileNotFoundError(f"masu.pngが見つかりません: {self.masu_image_path}")

        masu_area = self.crop_image_using_coords(self.scene_image_path, self.masu_coords)
        masu_area_path = os.path.join(self.screenshot_dir, 'masu_area.png')
        cv2.imwrite(masu_area_path, masu_area)

        if self.is_masu_present(masu_area, masu_img):
            self.log("[DoubleBattle] masu.png と一致!")

            # スクリーンショット
            screenshot_path = os.path.join(self.screenshot_dir, 'screenshot.png')
            self.takeScreenshot("Capture1", screenshot_path)
            haisinyou_image_path = os.path.join(self.haisin_dir, 'haisinyou.png')
            cv2.imwrite(haisinyou_image_path, scene_cropped_img)

            # 日付+時間でファイル名を作成（例: 20250916_123045.png）
            dt_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log(f"[DoubleBattle] DateTime: {dt_str}")

            new_screenshot_filename = f"{dt_str}.png"
            new_screenshot_path = os.path.join(self.hozon_dir, new_screenshot_filename)
            cv2.imwrite(new_screenshot_path, scene_cropped_img)
            self.log(f"[DoubleBattle] {new_screenshot_path} として保存")

            # タグ画像マッチング
            while self.is_masu_present(masu_area, masu_img):
                if self.stop_flag:
                    return
                self.takeScreenshot("Capture1", self.scene_image_path)
                masu_area = self.crop_image_using_coords(self.scene_image_path, self.masu_coords)
                cv2.imwrite(masu_area_path, masu_area)

                new_main_image_cv = cv2.imread(self.scene_image_path)
                tag_images_cv = [cv2.imread(path) for path in self.reference_image_paths]

                coords_list = [
                    (146, 138, 933, 255),
                    (146, 255, 933, 372),
                    (146, 372, 933, 489),
                    (146, 489, 933, 606),
                    (146, 606, 933, 723),
                    (146, 723, 933, 840)
                ]

                cropped_new_images = []
                for (x1, y1, x2, y2) in coords_list:
                    cropped_new_images.append(new_main_image_cv[y1:y2, x1:x2])

                matched_all = True
                matched_new_color_images = []

                for tag_idx, tag_img in enumerate(tag_images_cv):
                    matched = False
                    for cropped in cropped_new_images:
                        if (cropped.shape[0] >= tag_img.shape[0] and
                            cropped.shape[1] >= tag_img.shape[1]):
                            res = cv2.matchTemplate(cropped, tag_img, cv2.TM_CCOEFF_NORMED)
                            if np.any(res >= 0.8):
                                matched_new_color_images.append(cropped)
                                matched = True
                                break
                    if not matched:
                        self.log(f"[DoubleBattle] {tag_idx + 1}番目のタグが一致せず。")
                        matched_all = False
                        break

                if matched_all and len(matched_new_color_images) == 4:
                    combined_sorted_color_image_cv = cv2.vconcat(matched_new_color_images)
                    cv2.imwrite(self.combined_sorted_color_image_path_final, combined_sorted_color_image_cv)
                    self.log(f"[DoubleBattle] 結合画像保存: {self.combined_sorted_color_image_path_final}")

                time.sleep(1)

    # --- 元関数 ---
    def takeScreenshot(self, source, capName):
        with self.ws_lock:  # ロックを取得してOBSへアクセス
            response = self.ws.call(requests.TakeSourceScreenshot(
                sourceName=source, embedPictureFormat='png', width=None, height=None
            ))
        img_data = response.datain["img"].split(",")[1]
        img_data = img_data.encode('utf-8')
        missing_padding = len(img_data) % 4
        if missing_padding != 0:
            img_data += b'=' * (4 - missing_padding)
        with open(capName, 'wb') as f:
            f.write(base64.b64decode(img_data))

    def crop_image_using_coords(self, scene_img_path, coords):
        img = cv2.imread(scene_img_path)
        if img is None:
            raise FileNotFoundError(f"画像が見つかりません: {scene_img_path}")
        top_left = coords[0]
        bottom_right = coords[2]
        return img[int(top_left[1]):int(bottom_right[1]), int(top_left[0]):int(bottom_right[0])]

    def is_masu_present(self, scene_img, masu_img):
        result = cv2.matchTemplate(scene_img, masu_img, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return (max_val > 0.6)

    # 余分な OCR 用ヘルパーは削除済み

# ---------------------------------------------------
# rkaisi_teisi.py 相当
# ---------------------------------------------------
class RkaisiTeisiThread(threading.Thread):
    def __init__(self, ws, ws_lock, base_dir, log_text=None, logger=None):
        super().__init__()
        self.ws = ws
        self.ws_lock = ws_lock
        self.base_dir = base_dir
        self.stop_flag = False
        self.log_text = log_text
        self.logger = logger

        self.scene_image_path = os.path.join(self.base_dir, "scene2.png")
        self.masu1_template_path = os.path.join(self.base_dir, "masu1.png")
        self.mark_template_path = os.path.join(self.base_dir, "mark.png")
        self.masu1_cropped_path = os.path.join(self.base_dir, "masu1cropped.png")
        self.mark_cropped_path = os.path.join(self.base_dir, "markcropped.png")

        self.masu1_coords = ((1541.3, 229.4), (1651.1, 843.3))
        self.mark_coords = ((0, 0), (96, 72))
        self.MATCH_THRESHOLD = 0.4
        self.is_recording = False

    def run(self):
        self.log("[RkaisiTeisi] スレッド開始")
        try:
            while not self.stop_flag:
                self.main_loop()
        except Exception as e:
            self.log(f"[RkaisiTeisi] エラー: {e}")
        finally:
            if self.is_recording:
                self.log("[RkaisiTeisi] 録画停止を実行")
                with self.ws_lock:
                    self.ws.call(requests.StopRecording())
            self.log("[RkaisiTeisi] スレッド終了")

    def stop(self):
        self.stop_flag = True

    @thread_safe_log
    def log(self, message):
        print(message)
        if self.log_text:
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)

    def main_loop(self):
        self.take_screenshot("Capture1", self.scene_image_path)
        self.crop_image(self.scene_image_path, self.masu1_coords, self.masu1_cropped_path)
        self.crop_image(self.scene_image_path, self.mark_coords, self.mark_cropped_path)

        if (not self.is_recording) and self.is_template_present(self.masu1_cropped_path, self.masu1_template_path):
            self.log("[RkaisiTeisi] masu1検出 -> 録画開始")
            with self.ws_lock:
                self.ws.call(requests.StartRecording())
            self.is_recording = True
            time.sleep(100)

        elif self.is_recording and self.is_template_present(self.mark_cropped_path, self.mark_template_path):
            self.log("[RkaisiTeisi] mark検出 -> 録画停止")
            with self.ws_lock:
                self.ws.call(requests.StopRecording())
            self.is_recording = False

    def take_screenshot(self, source, save_path):
        with self.ws_lock:
            response = self.ws.call(requests.TakeSourceScreenshot(
                sourceName=source, embedPictureFormat='png', width=None, height=None
            ))
        if "img" not in response.datain:
            raise ValueError("OBSから画像データが取得できません。")
        img_data = response.datain["img"].split(",")[1]
        img_data = img_data.encode('utf-8')
        missing_padding = len(img_data) % 4
        if missing_padding != 0:
            img_data += b'=' * (4 - missing_padding)
        with open(save_path, 'wb') as f:
            f.write(base64.b64decode(img_data))
        self.log(f"[RkaisiTeisi] スクリーンショット: {save_path}")

    def crop_image(self, scene_path, coords, save_path):
        img = cv2.imread(scene_path)
        if img is None:
            raise FileNotFoundError(f"シーン画像が見つかりません: {scene_path}")
        (x1, y1), (x2, y2) = coords
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        cropped_img = img[y1:y2, x1:x2]
        cv2.imwrite(save_path, cropped_img)
        self.log(f"[RkaisiTeisi] 切り取り保存: {save_path}")

    def is_template_present(self, cropped_path, template_path):
        cropped_img = cv2.imread(cropped_path)
        template_img = cv2.imread(template_path)
        if cropped_img is None:
            self.log(f"[RkaisiTeisi] cropped_imgが読み込めません: {cropped_path}")
            return False
        if template_img is None:
            self.log(f"[RkaisiTeisi] テンプレートが読み込めません: {template_path}")
            return False
        result = cv2.matchTemplate(cropped_img, template_img, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        return max_val >= self.MATCH_THRESHOLD

# ---------------------------------------------------
# syouhai.py 相当
# ---------------------------------------------------
class SyouhaiThread(threading.Thread):
    def __init__(self, ws, ws_lock, base_dir, log_text=None, logger=None):
        super().__init__()
        self.ws = ws
        self.ws_lock = ws_lock
        self.base_dir = base_dir
        self.screenshot_dir = os.path.join(base_dir, 'handantmp')
        os.makedirs(self.screenshot_dir, exist_ok=True)
        self.scene_image_path = os.path.join(self.screenshot_dir, 'scene1.png')
        self.stop_flag = False
        self.log_text = log_text
        self.logger = logger

        self.coords = {
            "win": [(450, 990), (696, 1020)],
            "lose": [(480, 960), (730, 1045)],
            "disconnect": [(372, 654), (1548, 774)]
        }
        self.templates = {
            "win": os.path.join(self.screenshot_dir, 'win.png'),
            "lose": os.path.join(self.screenshot_dir, 'lose.png'),
            "disconnect": os.path.join(self.screenshot_dir, 'disconnect.png')
        }
        self.template_images = {}
        for key, path in self.templates.items():
            tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            self.template_images[key] = tpl

        self.counts = {"win": 0, "lose": 0, "disconnect": 0}

    def run(self):
        self.log("[Syouhai] スレッド開始")
        try:
            while not self.stop_flag:
                self.main_loop()
        except Exception as e:
            self.log(f"[Syouhai] エラー: {e}")
        finally:
            self.log("[Syouhai] スレッド終了")

    def stop(self):
        self.stop_flag = True

    @thread_safe_log
    def log(self, message):
        print(message)
        if self.log_text:
            self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)

    def main_loop(self):
        self.take_screenshot("Capture1", self.scene_image_path)
        scene_img = cv2.imread(self.scene_image_path)
        if scene_img is None:
            self.log("[Syouhai] 画像ロード失敗")
            return

        height, width = scene_img.shape[:2]
        self.log(f"[Syouhai] シーンサイズ: {width}x{height}")

        detected = False
        for region_name, region_coords in self.coords.items():
            if self.stop_flag:
                return
            top_left, bottom_right = region_coords
            if not (0 <= top_left[0] < width and 0 <= top_left[1] < height and
                    0 <= bottom_right[0] <= width and 0 <= bottom_right[1] <= height):
                self.log(f"[Syouhai] {region_name}座標が範囲外")
                continue

            cropped_img = scene_img[top_left[1]:bottom_right[1], top_left[0]:bottom_right[0]]
            if cropped_img is None or cropped_img.size == 0:
                self.log(f"[Syouhai] {region_name}切り取り失敗")
                continue

            template = self.template_images.get(region_name)
            if template is None:
                self.log(f"[Syouhai] テンプレート未読込: {region_name}")
                continue

            if self.is_template_present(cropped_img, template):
                self.counts[region_name] += 1
                self.log(f"[Syouhai] {region_name}検出 -> {self.counts[region_name]}")
                detected = True

        if detected:
            obs_text = f"勝ち: {self.counts['win']} - 負け: {self.counts['lose']} - アレ: {self.counts['disconnect']}"
            self.update_obs_text("sensekiText1", obs_text)
            time.sleep(60)

    def take_screenshot(self, source, cap_name):
        with self.ws_lock:
            response = self.ws.call(requests.TakeSourceScreenshot(
                sourceName=source, embedPictureFormat='png', width=None, height=None
            ))
        img_data = response.datain["img"].split(",")[1]
        with open(cap_name, 'wb') as f:
            f.write(base64.b64decode(img_data))

    def is_template_present(self, cropped_img, template, threshold=0.3):
        cropped_gray = cv2.cvtColor(cropped_img, cv2.COLOR_BGR2GRAY)
        result = cv2.matchTemplate(cropped_gray, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(result)
        self.log(f"[Syouhai] マッチ度: {max_val}")
        return (max_val > threshold)

    def update_obs_text(self, source_name, text):
        try:
            with self.ws_lock:
                self.ws.call(requests.SetSourceSettings(
                    sourceName=source_name,
                    sourceSettings={"text": text}
                ))
            self.log(f"[Syouhai] テキストソース更新: {text}")
        except Exception as e:
            self.log(f"[Syouhai] update_obs_text失敗: {e}")

# ---------------------------------------------------
# メインGUI
# ---------------------------------------------------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        # CustomTkinter グローバル設定（見た目）
        ctk.set_appearance_mode("Dark")  # "System" / "Light" / "Dark"
        ctk.set_default_color_theme("blue")  # "blue" / "dark-blue" / "green"

        self.title("OBS Tool (CustomTkinter)")
        self.geometry("1200x800")

        # 現在選択中のアクセントテーマを保持（UI再構築時に反映）
        self._accent_theme = "blue"

        # WS接続 & スレッド
        self.ws = None
        self.ws_lock = threading.Lock()  # すべてのOBS呼び出しに対する排他制御
        self.thread_double = None
        self.thread_rkaisi = None
        self.thread_syouhai = None

        # 画面部品
        self.create_widgets()

    def create_widgets(self):
        # レイアウト: 左サイドバー + 右ログ
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # タイトルバー風ラベル
        title = ctk.CTkLabel(self, text="OBS Screenshot / Template Tool", font=ctk.CTkFont(size=22, weight="bold"))
        title.grid(row=0, column=0, columnspan=2, sticky="we", padx=16, pady=(12, 0))

        # サイドバー
        sidebar = ctk.CTkFrame(self, corner_radius=10)
        sidebar.grid(row=1, column=0, sticky="nsw", padx=(16, 8), pady=12)
        sidebar.grid_rowconfigure(99, weight=1)

        # ---- OBS 接続エリア ----
        obs_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        obs_frame.grid(row=0, column=0, sticky="we", padx=8, pady=(8, 6))
        ctk.CTkLabel(obs_frame, text="OBS 接続", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))

        ctk.CTkLabel(obs_frame, text="Host").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        self.host_entry = ctk.CTkEntry(obs_frame, width=160)
        self.host_entry.insert(0, os.getenv("OBS_HOST", "localhost"))
        self.host_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Port").grid(row=2, column=0, sticky="e", padx=8, pady=4)
        self.port_entry = ctk.CTkEntry(obs_frame, width=120)
        self.port_entry.insert(0, os.getenv("OBS_PORT", "4444"))
        self.port_entry.grid(row=2, column=1, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(obs_frame, text="Password").grid(row=3, column=0, sticky="e", padx=8, pady=(4, 12))
        self.pass_entry = ctk.CTkEntry(obs_frame, show="*", width=160)
        self.pass_entry.insert(0, os.getenv("OBS_PASSWORD", ""))
        self.pass_entry.grid(row=3, column=1, sticky="w", padx=8, pady=(4, 12))

        # ---- パス設定 ----
        path_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        path_frame.grid(row=1, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(path_frame, text="パス設定", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 4))
        ctk.CTkLabel(path_frame, text="ベースディレクトリ").grid(row=1, column=0, sticky="e", padx=8, pady=(4, 12))
        self.base_dir_entry = ctk.CTkEntry(path_frame, width=220)
        self.base_dir_entry.insert(0, os.getenv("BASE_DIR", os.getcwd()))
        self.base_dir_entry.grid(row=1, column=1, sticky="w", padx=8, pady=(4, 12))
        ctk.CTkButton(path_frame, text="参照", command=self.browse_base_dir).grid(row=1, column=2, sticky="w", padx=8, pady=(4, 12))

        # ---- スクリプト選択 ----
        script_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        script_frame.grid(row=2, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(script_frame, text="実行するスクリプト", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=8, pady=(8, 4))

        self.chk_double_var = tk.BooleanVar(value=True)
        self.chk_rkaisi_var = tk.BooleanVar(value=True)
        self.chk_syouhai_var = tk.BooleanVar(value=True)

        ctk.CTkCheckBox(script_frame, text="double_battle (ダブルバトル)", variable=self.chk_double_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkCheckBox(script_frame, text="rkaisi_teisi (録画開始・停止)", variable=self.chk_rkaisi_var).pack(anchor="w", padx=8, pady=2)
        ctk.CTkCheckBox(script_frame, text="syouhai (勝敗判定)", variable=self.chk_syouhai_var).pack(anchor="w", padx=8, pady=(2, 8))

        # ---- コントロール ----
        control_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        control_frame.grid(row=3, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkLabel(control_frame, text="操作", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))
        ctk.CTkButton(control_frame, text="Start", command=self.start_threads, height=36).grid(row=1, column=0, sticky="we", padx=8, pady=6)
        ctk.CTkButton(control_frame, text="Stop", command=self.stop_threads, height=36, fg_color="#8A1C1C").grid(row=1, column=1, sticky="we", padx=8, pady=6)

        # ---- テーマ設定 ----
        theme_frame = ctk.CTkFrame(sidebar, corner_radius=10)
        theme_frame.grid(row=4, column=0, sticky="we", padx=8, pady=(6, 12))
        ctk.CTkLabel(theme_frame, text="テーマ", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(8, 4))
        ctk.CTkLabel(theme_frame, text="外観").grid(row=1, column=0, sticky="e", padx=8, pady=4)
        appearance_opt = ctk.CTkOptionMenu(theme_frame, values=["System", "Light", "Dark"], command=self._change_appearance)
        appearance_opt.set("Dark")
        appearance_opt.grid(row=1, column=1, sticky="w", padx=8, pady=4)
        ctk.CTkLabel(theme_frame, text="アクセント").grid(row=2, column=0, sticky="e", padx=8, pady=(4, 12))
        theme_opt = ctk.CTkOptionMenu(theme_frame, values=["blue", "dark-blue", "green"], command=self._change_theme)
        theme_opt.set(self._accent_theme)
        theme_opt.grid(row=2, column=1, sticky="w", padx=8, pady=(4, 12))

        # 右側: ログ
        right = ctk.CTkFrame(self, corner_radius=10)
        right.grid(row=1, column=1, sticky="nsew", padx=(8, 16), pady=12)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="ログ出力", font=ctk.CTkFont(weight="bold")).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 6))
        log_container = ctk.CTkFrame(right)
        log_container.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        log_container.grid_rowconfigure(0, weight=1)
        log_container.grid_columnconfigure(0, weight=1)

        self.log_text = ctk.CTkTextbox(log_container, wrap="word")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scrollbar = ctk.CTkScrollbar(log_container, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scrollbar.set)

    def browse_base_dir(self):
        path = fd.askdirectory(title="ベースディレクトリ選択")
        if path:
            self.base_dir_entry.delete(0, tk.END)
            self.base_dir_entry.insert(0, path)

    def _change_appearance(self, mode: str):
        try:
            ctk.set_appearance_mode(mode)
        except Exception:
            pass

    def _change_theme(self, theme: str):
        try:
            ctk.set_default_color_theme(theme)
            self._accent_theme = theme
            # 既存ウィジェットに新テーマを反映するため UI を再構築
            if self._any_threads_alive():
                mb.showinfo("テーマ変更", "スレッド実行中はアクセント色の即時反映ができません。\nStop 後にもう一度お試しください。")
                return
            self._rebuild_ui_preserving_state()
        except Exception as e:
            mb.showerror("テーマ変更エラー", str(e))

    def _any_threads_alive(self) -> bool:
        try:
            if self.thread_double and self.thread_double.is_alive():
                return True
        except Exception:
            pass
        try:
            if self.thread_rkaisi and self.thread_rkaisi.is_alive():
                return True
        except Exception:
            pass
        try:
            if self.thread_syouhai and self.thread_syouhai.is_alive():
                return True
        except Exception:
            pass
        return False

    def _rebuild_ui_preserving_state(self):
        # 入力状態とログを保存
        host = getattr(self, 'host_entry', None).get() if getattr(self, 'host_entry', None) else os.getenv("OBS_HOST", "localhost")
        port = getattr(self, 'port_entry', None).get() if getattr(self, 'port_entry', None) else os.getenv("OBS_PORT", "4444")
        password = getattr(self, 'pass_entry', None).get() if getattr(self, 'pass_entry', None) else os.getenv("OBS_PASSWORD", "")
        base_dir = getattr(self, 'base_dir_entry', None).get() if getattr(self, 'base_dir_entry', None) else os.getcwd()
        chk_double = getattr(self, 'chk_double_var', tk.BooleanVar(value=True)).get()
        chk_rkaisi = getattr(self, 'chk_rkaisi_var', tk.BooleanVar(value=True)).get()
        chk_syouhai = getattr(self, 'chk_syouhai_var', tk.BooleanVar(value=True)).get()
        log_content = ""
        if getattr(self, 'log_text', None):
            try:
                log_content = self.log_text.get("1.0", "end-1c")
            except Exception:
                log_content = ""

        # 既存 UI を破棄
        for child in self.winfo_children():
            try:
                child.destroy()
            except Exception:
                pass

        # 再構築
        self.create_widgets()

        # 状態を復元
        self.host_entry.delete(0, tk.END); self.host_entry.insert(0, host)
        self.port_entry.delete(0, tk.END); self.port_entry.insert(0, port)
        self.pass_entry.delete(0, tk.END); self.pass_entry.insert(0, password)
        self.base_dir_entry.delete(0, tk.END); self.base_dir_entry.insert(0, base_dir)
        self.chk_double_var.set(chk_double)
        self.chk_rkaisi_var.set(chk_rkaisi)
        self.chk_syouhai_var.set(chk_syouhai)
        if log_content:
            try:
                self.log_text.insert("1.0", log_content)
            except Exception:
                pass

    def start_threads(self):
        host = self.host_entry.get()
        port = int(self.port_entry.get())
        password = self.pass_entry.get()
        base_dir = self.base_dir_entry.get()

        # 既に接続済みなら切断して再接続
        if self.ws:
            try:
                self.ws.disconnect()
            except:
                pass
            self.ws = None

        # OBSに接続
        try:
            self.ws = obsws(host, port, password)
            self.ws.connect()
            mb.showinfo("接続完了", f"OBS WebSocketに接続しました: {host}:{port}")
            self.log_text.insert(tk.END, "[App] OBSに接続成功\n")
        except Exception as e:
            mb.showerror("接続失敗", f"OBSに接続できませんでした。\n{e}")
            self.log_text.insert(tk.END, f"[App] 接続失敗: {e}\n")
            return

        # スレッド開始
        if self.chk_double_var.get():
            self.thread_double = DoubleBattleThread(self.ws, self.ws_lock, base_dir, self.log_text)
            self.thread_double.start()
        if self.chk_rkaisi_var.get():
            handantmp_dir = os.path.join(base_dir, 'handantmp')
            self.thread_rkaisi = RkaisiTeisiThread(self.ws, self.ws_lock, handantmp_dir, self.log_text)
            self.thread_rkaisi.start()
        if self.chk_syouhai_var.get():
            self.thread_syouhai = SyouhaiThread(self.ws, self.ws_lock, base_dir, self.log_text)
            self.thread_syouhai.start()

    def stop_threads(self):
        # スレッド停止
        if self.thread_double and self.thread_double.is_alive():
            self.thread_double.stop()
        if self.thread_rkaisi and self.thread_rkaisi.is_alive():
            self.thread_rkaisi.stop()
        if self.thread_syouhai and self.thread_syouhai.is_alive():
            self.thread_syouhai.stop()

        # OBS切断
        if self.ws:
            try:
                self.ws.disconnect()
                self.log_text.insert(tk.END, "[App] OBS切断\n")
            except:
                pass
            self.ws = None

        mb.showinfo("停止", "全スレッド停止を指示しました。")

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
