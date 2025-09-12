import os
import time
import cv2
import numpy as np
import base64
import pytesseract
import unidecode
import threading
import datetime
import tkinter as tk
import tkinter.filedialog as fd
import tkinter.messagebox as mb
import tkinter.scrolledtext as st

from PIL import Image, ImageEnhance, ImageFilter
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
    def __init__(self, ws, ws_lock, base_dir, tesseract_path, log_text=None, logger=None):
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

        pytesseract.pytesseract.tesseract_cmd = tesseract_path

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

            # ----------------------------
            # OCR 処理を日時に置換
            # ----------------------------
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.log(f"[DoubleBattle] Timestamp: {timestamp}")

            sanitized_text = timestamp  # OCR ではなく日時を使用
            counter = 1
            new_screenshot_filename = f"{sanitized_text}.png"
            new_screenshot_path = os.path.join(self.hozon_dir, new_screenshot_filename)
            while os.path.exists(new_screenshot_path):
                new_screenshot_filename = f"{sanitized_text}_{counter}.png"
                new_screenshot_path = os.path.join(self.hozon_dir, new_screenshot_filename)
                counter += 1
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

    # OCR メソッドは未使用だが、一応残しておく
    def ocr(self, image_path):
        image_cv = cv2.imread(image_path)
        if image_cv is None:
            raise FileNotFoundError(f"画像が見つかりません: {image_path}")
        image_rgb = cv2.cvtColor(image_cv, cv2.COLOR_BGR2RGB)
        image_pil = Image.fromarray(image_rgb)
        width, height = image_pil.size
        cropped_image = image_pil.crop((0, 0, width, 70))
        gray_image = cropped_image.convert('L')
        enhancer = ImageEnhance.Contrast(gray_image)
        enhanced_image = enhancer.enhance(2).filter(ImageFilter.SHARPEN)
        filtered_image = enhanced_image.filter(ImageFilter.MedianFilter(size=3))
        binary_image = filtered_image.point(lambda p: 255 if p > 130 else 0)
        recognized_text = pytesseract.image_to_string(
            binary_image,
            lang="jpn+eng",
            config="--psm 6 --oem 3"
        )
        return recognized_text.strip()

    def sanitize_filename(self, name):
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '_')
        safe_name = name.encode('utf-8', 'ignore').decode('utf-8')
        return unidecode.unidecode(safe_name)

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
class App(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("OBS Tool with Improved UI")
        self.geometry("1920x1080")

        # ttkのテーマ設定
        s = ttk.Style()
        s.theme_use("clam")  # 好みのテーマに変更可

        # WS接続 & スレッド
        self.ws = None
        self.ws_lock = threading.Lock()  # すべてのOBS呼び出しに対する排他制御
        self.thread_double = None
        self.thread_rkaisi = None
        self.thread_syouhai = None

        # 画面部品
        self.create_widgets()

    def create_widgets(self):
        # メインフレーム
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # -------------------------------------------------
        # 上段: OBS接続設定
        # -------------------------------------------------
        obs_frame = ttk.Labelframe(main_frame, text="OBS接続情報", padding=10)
        obs_frame.pack(fill=tk.X)

        ttk.Label(obs_frame, text="Host:").grid(row=0, column=0, sticky=tk.E, padx=5, pady=3)
        self.host_entry = ttk.Entry(obs_frame, width=15)
        self.host_entry.insert(0, os.getenv("OBS_HOST", "localhost"))
        self.host_entry.grid(row=0, column=1, padx=5, pady=3)

        ttk.Label(obs_frame, text="Port:").grid(row=0, column=2, sticky=tk.E, padx=5, pady=3)
        self.port_entry = ttk.Entry(obs_frame, width=7)
        self.port_entry.insert(0, os.getenv("OBS_PORT", "4444"))
        self.port_entry.grid(row=0, column=3, padx=5, pady=3)

        ttk.Label(obs_frame, text="Password:").grid(row=1, column=0, sticky=tk.E, padx=5, pady=3)
        self.pass_entry = ttk.Entry(obs_frame, show="*", width=15)
        self.pass_entry.insert(0, os.getenv("OBS_PASSWORD", ""))
        self.pass_entry.grid(row=1, column=1, padx=5, pady=3, sticky=tk.W)

        # -------------------------------------------------
        # 中段: Tesseract & ベースディレクトリ設定
        # -------------------------------------------------
        path_frame = ttk.Labelframe(main_frame, text="パス設定", padding=10)
        path_frame.pack(fill=tk.X, pady=5)

        ttk.Label(path_frame, text="Tesseract.exe:").grid(row=0, column=0, sticky=tk.E, padx=5, pady=3)
        self.tess_entry = ttk.Entry(path_frame, width=40)
        self.tess_entry.insert(0, os.getenv("TESSERACT_PATH", r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"))
        self.tess_entry.grid(row=0, column=1, padx=5, pady=3)
        ttk.Button(path_frame, text="参照", command=self.browse_tesseract).grid(row=0, column=2, padx=5, pady=3)

        ttk.Label(path_frame, text="ベースディレクトリ:").grid(row=1, column=0, sticky=tk.E, padx=5, pady=3)
        self.base_dir_entry = ttk.Entry(path_frame, width=40)
        self.base_dir_entry.insert(0, os.getenv("BASE_DIR", os.getcwd()))
        self.base_dir_entry.grid(row=1, column=1, padx=5, pady=3)
        ttk.Button(path_frame, text="参照", command=self.browse_base_dir).grid(row=1, column=2, padx=5, pady=3)

        # -------------------------------------------------
        # スクリプト選択
        # -------------------------------------------------
        script_frame = ttk.Labelframe(main_frame, text="実行するスクリプト", padding=10)
        script_frame.pack(fill=tk.X, pady=5)

        self.chk_double_var = tk.BooleanVar(value=True)
        self.chk_rkaisi_var = tk.BooleanVar(value=True)
        self.chk_syouhai_var = tk.BooleanVar(value=True)

        ttk.Checkbutton(script_frame, text="double_battle(ダブルバトル)", variable=self.chk_double_var).pack(anchor=tk.W)
        ttk.Checkbutton(script_frame, text="rkaisi_teisi(録画開始・停止)", variable=self.chk_rkaisi_var).pack(anchor=tk.W)
        ttk.Checkbutton(script_frame, text="syouhai(勝敗判定)", variable=self.chk_syouhai_var).pack(anchor=tk.W)

        # -------------------------------------------------
        # 実行／停止ボタン
        # -------------------------------------------------
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="Start", command=self.start_threads).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Stop", command=self.stop_threads).pack(side=tk.LEFT, padx=5)

        # -------------------------------------------------
        # 下段: ログ表示 (ScrolledText)
        # -------------------------------------------------
        log_frame = ttk.Labelframe(main_frame, text="ログ出力", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = st.ScrolledText(log_frame, wrap=tk.WORD, height=8)
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def browse_tesseract(self):
        path = fd.askopenfilename(
            title="Tesseract実行ファイルを選択",
            filetypes=[("実行ファイル", "*.exe"), ("すべてのファイル", "*.*")]
        )
        if path:
            self.tess_entry.delete(0, tk.END)
            self.tess_entry.insert(0, path)

    def browse_base_dir(self):
        path = fd.askdirectory(title="ベースディレクトリ選択")
        if path:
            self.base_dir_entry.delete(0, tk.END)
            self.base_dir_entry.insert(0, path)

    def start_threads(self):
        host = self.host_entry.get()
        port = int(self.port_entry.get())
        password = self.pass_entry.get()
        tesseract_path = self.tess_entry.get()
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
            self.thread_double = DoubleBattleThread(self.ws, self.ws_lock, base_dir, tesseract_path, self.log_text)
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
