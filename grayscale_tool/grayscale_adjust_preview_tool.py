# grayscale_adjust_preview_tool.py
# グレースケール調整ツール（ライブプレビュー + レスポンシブ）
# 出力先：ユーザーの デスクトップ\outputfolder（固定）
# 依存：Pillow（pip install pillow）

from __future__ import annotations
import os
import sys
import time
import subprocess
from typing import Iterable, List, Tuple, Dict, Any, Optional

from PIL import (
    Image, ImageOps, ImageEnhance, ImageTk, UnidentifiedImageError
)
import tkinter as tk
from tkinter import filedialog, messagebox

# ---------------- 定数 ----------------
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}
JPEG_SAVE_KW = dict(quality=95, subsampling=0, optimize=True, progressive=False)
DEBOUNCE_MS = 120  # リサイズ/スライダーの反映待ち（チラつき防止）

# ----------- 出力先（Desktop固定） -----------
def get_output_dir() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out = os.path.join(desktop, "outputfolder")
    os.makedirs(out, exist_ok=True)
    return out

OUTPUT_DIR = get_output_dir()

# ---------------- ユーティリティ ----------------
def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS

def norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))

def collect_targets(inputs: Iterable[str]) -> List[str]:
    paths: List[str] = []
    seen = set()
    for p in inputs:
        if not p: continue
        p = norm(p)
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    fp = norm(os.path.join(root, fn))
                    if is_image_file(fp) and fp not in seen:
                        seen.add(fp); paths.append(fp)
        elif os.path.isfile(p) and is_image_file(p):
            if p not in seen:
                seen.add(p); paths.append(p)
    paths.sort()
    return paths

def safe_out_path(filename: str, suffix: str) -> str:
    name, ext = os.path.splitext(filename)
    dst = os.path.join(OUTPUT_DIR, f"{name}{suffix}{ext}")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(OUTPUT_DIR, f"{name}{suffix}-{n}{ext}")
        n += 1
    return dst

def resize_to_box(im: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """画像を枠内に収まるよう等倍縮小（拡大はしない）"""
    w, h = im.size
    if w == 0 or h == 0 or max_w <= 0 or max_h <= 0:
        return im
    scale = min(1.0, max_w / w, max_h / h)
    if scale < 1.0:
        nw, nh = int(w * scale), int(h * scale)
        return im.resize((nw, nh), Image.LANCZOS)
    return im

# --------------- 調整ロジック ---------------
def to_gray_L(im: Image.Image) -> Image.Image:
    if im.mode not in ("L", "LA"):
        im = im.convert("RGB")
    return ImageOps.grayscale(im)

def apply_adjustments_L(gray: Image.Image, cfg: Dict[str, Any]) -> Image.Image:
    im = gray
    if cfg.get("autocontrast", False):
        im = ImageOps.autocontrast(im, cutoff=int(cfg.get("cutoff", 1)))
    b = float(cfg.get("brightness", 1.0))
    if abs(b - 1.0) > 1e-6:
        im = ImageEnhance.Brightness(im).enhance(b)
    c = float(cfg.get("contrast", 1.0))
    if abs(c - 1.0) > 1e-6:
        im = ImageEnhance.Contrast(im).enhance(c)
    g = float(cfg.get("gamma", 1.0))
    if abs(g - 1.0) > 1e-6:
        inv = 1.0 / max(g, 1e-6)
        lut = [int(round((i / 255.0) ** inv * 255.0)) for i in range(256)]
        im = im.point(lut, mode="L")
    return im

# --------------- 保存（フル解像度） ---------------
def convert_fullres(src: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        with Image.open(src) as im:
            if getattr(im, "is_animated", False):
                return False, "アニメGIFのためスキップ"
            out = apply_adjustments_L(to_gray_L(im), cfg)
            base = os.path.basename(src)
            dst = safe_out_path(base, "_adj")
            ext = os.path.splitext(dst)[1].lower()
            save_kw = {}
            if ext in (".jpg", ".jpeg"):
                save_kw.update(JPEG_SAVE_KW)  # EXIF/ICCは渡さない
            out.save(dst, **save_kw)
            return True, dst
    except UnidentifiedImageError:
        return False, "不正な画像/未対応の形式"
    except Exception as e:
        return False, f"失敗: {e}"

# --------------- プレビュー ---------------
def make_preview_base(path: str) -> Optional[Image.Image]:
    try:
        with Image.open(path) as im:
            return to_gray_L(im)  # 原寸Lを保持、表示時にフィット
    except Exception:
        return None

def pil_to_tk(im: Image.Image) -> ImageTk.PhotoImage:
    return ImageTk.PhotoImage(im)

# --------------- GUI ---------------
class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("グレースケール調整ツール（ライブプレビュー）")
        self.root.geometry("1080x680")
        self.root.minsize(720, 480)

        # 左：ファイル操作
        left = tk.Frame(self.root); left.pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=8)
        tk.Button(left, text="ファイル追加", command=self.add_files).pack(fill=tk.X, pady=2)
        tk.Button(left, text="フォルダ追加（再帰）", command=self.add_folder).pack(fill=tk.X, pady=2)
        tk.Button(left, text="一覧クリア", command=self.clear_list).pack(fill=tk.X, pady=2)
        tk.Label(left, text="ファイル一覧").pack(anchor="w", pady=(8,2))
        self.lb = tk.Listbox(left, width=45); self.lb.pack(fill=tk.BOTH, expand=True)
        self.lb.bind("<<ListboxSelect>>", self.on_select)

        # 右：プレビュー＆調整
        right = tk.Frame(self.root); right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)

        # プレビュー（左右並列）
        pv = tk.LabelFrame(right, text="プレビュー（左: Before / 右: After）")
        pv.pack(fill=tk.BOTH, expand=True)
        self.pv = pv
        self.canvas_before = tk.Label(pv, bd=1, relief="sunken")
        self.canvas_after  = tk.Label(pv, bd=1, relief="sunken")
        self.canvas_before.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.canvas_after .pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        self.tkimg_before: Optional[ImageTk.PhotoImage] = None
        self.tkimg_after:  Optional[ImageTk.PhotoImage] = None
        pv.bind("<Configure>", lambda _e: self.schedule_update())  # 枠サイズ変更で再描画

        # 調整パネル
        panel = tk.LabelFrame(right, text="調整"); panel.pack(fill=tk.X, pady=6)
        self.var_ac = tk.IntVar(value=0)
        tk.Checkbutton(panel, text="オートコントラスト",
                       variable=self.var_ac, command=self.schedule_update).grid(row=0, column=0, sticky="w", padx=6, pady=6)
        tk.Label(panel, text="cutoff(0-5)").grid(row=0, column=1, sticky="e")
        self.spin_cutoff = tk.Spinbox(panel, from_=0, to=5, width=5, command=self.schedule_update)
        self.spin_cutoff.delete(0, "end"); self.spin_cutoff.insert(0, "1")
        self.spin_cutoff.grid(row=0, column=2, sticky="w", padx=6)

        def slider(text, frm, to, init):
            row = panel.grid_size()[1]
            tk.Label(panel, text=text).grid(row=row, column=0, sticky="w", padx=6)
            s = tk.Scale(panel, from_=frm, to=to, resolution=0.01,
                         orient="horizontal", length=360,
                         command=lambda _v: self.schedule_update())
            s.set(init); s.grid(row=row, column=1, columnspan=2, sticky="we", padx=6)
            return s

        self.scale_b = slider("明るさ 0.50–1.50", 0.50, 1.50, 1.00)
        self.scale_c = slider("コントラスト 0.50–1.50", 0.50, 1.50, 1.00)
        self.scale_g = slider("ガンマ 0.70–1.30", 0.70, 1.30, 1.00)

        # 実行
        btns = tk.Frame(right); btns.pack(fill=tk.X, pady=(4,0))
        tk.Button(btns, text="選択中だけ変換", command=self.convert_selected).pack(side=tk.LEFT, padx=4)
        tk.Button(btns, text="一覧すべて変換", command=self.convert_all).pack(side=tk.LEFT, padx=4)
        tk.Label(btns, text=f"出力先: {OUTPUT_DIR}").pack(side=tk.RIGHT, padx=6)

        # 状態
        self.targets: List[str] = []
        self.preview_base: Optional[Image.Image] = None
        self.update_job: Optional[str] = None

    # ------------- イベント -------------
    def add_files(self):
        files = filedialog.askopenfilenames(
            title="画像ファイルを選択",
            filetypes=[("画像", "*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp;*.gif"), ("すべて", "*.*")]
        )
        self._push(list(files))

    def add_folder(self):
        d = filedialog.askdirectory(title="フォルダを選択")
        if d: self._push([d])

    def _push(self, items: List[str]):
        new = collect_targets(items)
        exist = set(self.targets)
        added = [p for p in new if p not in exist]
        if not added: return
        self.targets += added
        for p in added: self.lb.insert(tk.END, p)
        if len(self.targets) == len(added):
            self.lb.selection_set(0); self.on_select()

    def clear_list(self):
        self.targets.clear(); self.lb.delete(0, tk.END)
        self.preview_base = None
        self.set_preview(None, None)

    def on_select(self, _evt=None):
        sel = self.get_selected_path()
        if not sel: return
        self.preview_base = make_preview_base(sel)
        if self.preview_base is None:
            messagebox.showwarning("読み込みエラー", "プレビュー用の画像を作成できませんでした。"); return
        self.render_preview()

    def get_selected_path(self) -> Optional[str]:
        sel = self.lb.curselection()
        if not sel: return None
        idx = sel[0]
        if 0 <= idx < len(self.targets):
            return self.targets[idx]
        return None

    # ------------- プレビュー更新 -------------
    def schedule_update(self):
        if self.update_job:
            self.root.after_cancel(self.update_job)
        self.update_job = self.root.after(DEBOUNCE_MS, self.render_preview)

    def render_preview(self):
        self.update_job = None
        if self.preview_base is None:
            self.set_preview(None, None); return

        # プレビュー枠の実サイズ
        total_w = max(self.pv.winfo_width() - 16, 100)
        total_h = max(self.pv.winfo_height() - 16, 100)
        each_w, each_h = total_w // 2, total_h

        cfg = self.get_cfg()
        before_full = self.preview_base
        after_full  = apply_adjustments_L(self.preview_base, cfg)

        # 枠にフィット
        before = resize_to_box(before_full, each_w, each_h)
        after  = resize_to_box(after_full,  each_w, each_h)
        self.set_preview(before, after)

    def set_preview(self, im_before: Optional[Image.Image], im_after: Optional[Image.Image]):
        if im_before is None or im_after is None:
            self.canvas_before.config(image=""); self.canvas_after.config(image="")
            self.tkimg_before = None; self.tkimg_after = None
            return
        tkb = pil_to_tk(im_before); tka = pil_to_tk(im_after)
        self.canvas_before.config(image=tkb); self.canvas_after.config(image=tka)
        self.tkimg_before = tkb; self.tkimg_after = tka  # 参照保持

    def get_cfg(self) -> Dict[str, Any]:
        return {
            "autocontrast": bool(self.var_ac.get()),
            "cutoff": int(self.spin_cutoff.get() or 1),
            "brightness": float(self.scale_b.get()),
            "contrast": float(self.scale_c.get()),
            "gamma": float(self.scale_g.get()),
        }

    # ------------- 変換実行 -------------
    def convert_selected(self):
        sel = self.get_selected_path()
        if not sel:
            messagebox.showwarning("警告", "変換するファイルを選択してください。"); return
        cfg = self.get_cfg()
        ok, info = convert_fullres(sel, cfg)
        if ok:
            messagebox.showinfo("完了", f"保存しました:\n{info}\n\n出力先: {OUTPUT_DIR}")
            try: subprocess.Popen(f'explorer "{OUTPUT_DIR}"')
            except Exception: pass
        else:
            messagebox.showerror("失敗", f"{info}")

    def convert_all(self):
        if not self.targets:
            messagebox.showwarning("警告", "処理対象がありません。"); return
        cfg = self.get_cfg()
        ok = skip = 0; start = time.time()
        for p in self.targets:
            res, _ = convert_fullres(p, cfg)
            ok += int(res); skip += int(not res); self.root.update()
        dur = time.time() - start
        messagebox.showinfo("完了", f"一括変換が完了しました。\n成功: {ok} / スキップ: {skip}\n所要時間: {dur:.2f} 秒\n出力先: {OUTPUT_DIR}")
        try: subprocess.Popen(f'explorer "{OUTPUT_DIR}"')
        except Exception: pass

    def run(self):
        self.root.mainloop()

# --------------- エントリ ---------------
def main(argv: List[str]) -> int:
    app = App()
    app.run()
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
