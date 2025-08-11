# grayscale_tool.py
# 出力先：ユーザーのデスクトップ\grayscale_output（必ずここ）
# 仕様：exe/.py のどちらでも同じ挙動。EXIF/ICCは渡さず保存＝サムネも完全グレー表示。

from __future__ import annotations
import os
import sys
import time
import subprocess
from typing import Iterable, List, Tuple

from PIL import Image, ImageOps, UnidentifiedImageError  # pip install pillow

# 対応拡張子
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}

# JPEG保存設定（EXIF/ICCは渡さない）
JPEG_SAVE_KW = dict(
    quality=95,
    subsampling=0,
    optimize=True,
    progressive=False,
)

# ---------- 出力先（常にDesktop） ----------
def get_desktop_output_dir() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out = os.path.join(desktop, "grayscale_output")
    os.makedirs(out, exist_ok=True)
    return out

OUTPUT_DIR = get_desktop_output_dir()  # 起動時に作成しておく

# ---------- ユーティリティ ----------
def is_image_file(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS

def norm(path: str) -> str:
    return os.path.normpath(os.path.abspath(path))

def collect_targets(inputs: Iterable[str]) -> List[str]:
    paths: List[str] = []
    seen = set()
    for p in inputs:
        if not p:
            continue
        p = norm(p)
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in files:
                    fp = norm(os.path.join(root, fn))
                    if is_image_file(fp) and fp not in seen:
                        seen.add(fp)
                        paths.append(fp)
        elif os.path.isfile(p) and is_image_file(p):
            if p not in seen:
                seen.add(p)
                paths.append(p)
    paths.sort()
    return paths

def safe_out_path(filename: str) -> str:
    name, ext = os.path.splitext(filename)
    dst = os.path.join(OUTPUT_DIR, f"{name}_gray{ext}")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(OUTPUT_DIR, f"{name}_gray-{n}{ext}")
        n += 1
    return dst

# ---------- 変換本体 ----------
def convert_to_gray(src: str) -> Tuple[bool, str]:
    """
    1枚処理。成功 (True, 出力パス) / 失敗 (False, 理由)
    - アニメGIFはスキップ
    - EXIF/ICCは付与しない（サムネがカラーになるのを防ぐ）
    """
    try:
        with Image.open(src) as im:
            if getattr(im, "is_animated", False):
                return False, "アニメGIFのためスキップ"

            # まずRGB化→グレースケール化（チャンネルを確実に1chへ）
            if im.mode not in ("L", "LA"):
                im = im.convert("RGB")
            gray = ImageOps.grayscale(im)  # = convert("L")

            base = os.path.basename(src)
            dst = safe_out_path(base)

            ext = os.path.splitext(dst)[1].lower()
            save_kw = {}
            if ext in (".jpg", ".jpeg"):
                save_kw.update(JPEG_SAVE_KW)
            # PNG/TIFF/BMP/GIFは特に指定不要（ICCやEXIFは渡さない）

            gray.save(dst, **save_kw)
            return True, dst

    except UnidentifiedImageError:
        return False, "不正な画像/未対応の形式"
    except Exception as e:
        return False, f"失敗: {e}"

# ---------- 実行ラッパ ----------
def run_cli(argv: List[str]) -> int:
    targets = collect_targets(argv)
    if not targets:
        return launch_gui()
    return process_targets(targets)

def process_targets(targets: List[str]) -> int:
    ok = skip = 0
    start = time.time()
    for i, p in enumerate(targets, 1):
        res, info = convert_to_gray(p)
        if res:
            ok += 1
            print(f"[{i}/{len(targets)}] OK  : {os.path.basename(p)} -> {os.path.basename(info)}")
        else:
            skip += 1
            print(f"[{i}/{len(targets)}] SKIP: {os.path.basename(p)} ({info})")
    dur = time.time() - start
    print("-" * 60)
    print(f"出力先: {OUTPUT_DIR}")
    print(f"完了: 成功 {ok} / スキップ {skip} / 合計 {len(targets)} | {dur:.2f}s")
    try:
        subprocess.Popen(f'explorer "{OUTPUT_DIR}"')
    except Exception:
        pass
    return 0

# ---------- GUI ----------
def launch_gui() -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    targets: List[str] = []

    def add_files():
        files = filedialog.askopenfilenames(
            title="画像ファイルを選択",
            filetypes=[("画像", "*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp;*.gif"), ("すべて", "*.*")]
        )
        _push(list(files))

    def add_folder():
        d = filedialog.askdirectory(title="フォルダを選択")
        if d:
            _push([d])

    def _push(items: List[str]):
        nonlocal targets
        new = collect_targets(items)
        exist = set(targets)
        added = [p for p in new if p not in exist]
        if not added:
            return
        targets += added
        for p in added:
            lb.insert(tk.END, p)
        lbl_count.set(f"{len(targets)} 件")

    def clear_list():
        targets.clear()
        lb.delete(0, tk.END)
        lbl_count.set("0 件")

    def start_proc():
        if not targets:
            messagebox.showwarning("警告", "処理対象がありません。ファイルまたはフォルダを追加してください。")
            return
        btn_start.config(state=tk.DISABLED)
        root.update_idletasks()

        ok = skip = 0
        start = time.time()
        for i, p in enumerate(targets, 1):
            res, info = convert_to_gray(p)
            if res:
                ok += 1
                log(f"[{i}/{len(targets)}] OK  : {os.path.basename(p)} → {os.path.basename(info)}")
            else:
                skip += 1
                log(f"[{i}/{len(targets)}] SKIP: {os.path.basename(p)} （{info}）")
            root.update()

        dur = time.time() - start
        messagebox.showinfo("完了",
                            f"変換が完了しました。\n成功: {ok} / スキップ: {skip}\n所要時間: {dur:.2f} 秒\n出力先: {OUTPUT_DIR}")
        try:
            subprocess.Popen(f'explorer "{OUTPUT_DIR}"')
        except Exception:
            pass
        btn_start.config(state=tk.NORMAL)

    def log(msg: str):
        txt_log.insert(tk.END, msg + "\n")
        txt_log.see(tk.END)

    root = tk.Tk()
    root.title("グレースケール変換（Pillow・Desktop出力・EXIF除去）")
    root.geometry("760x520")

    frm_top = tk.Frame(root); frm_top.pack(fill=tk.X, padx=12, pady=10)
    tk.Button(frm_top, text="ファイル追加", command=add_files).pack(side=tk.LEFT, padx=4)
    tk.Button(frm_top, text="フォルダ追加（再帰）", command=add_folder).pack(side=tk.LEFT, padx=4)
    tk.Button(frm_top, text="一覧クリア", command=clear_list).pack(side=tk.LEFT, padx=4)

    lbl_count = tk.StringVar(value="0 件")
    tk.Label(frm_top, textvariable=lbl_count).pack(side=tk.RIGHT)

    frm_mid = tk.Frame(root); frm_mid.pack(fill=tk.BOTH, expand=True, padx=12)
    lb = tk.Listbox(frm_mid); lb.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
    sb = tk.Scrollbar(frm_mid, orient="vertical", command=lb.yview); sb.pack(side=tk.LEFT, fill=tk.Y)
    lb.config(yscrollcommand=sb.set)

    frm_bottom = tk.Frame(root); frm_bottom.pack(fill=tk.BOTH, expand=False, padx=12, pady=8)
    btn_start = tk.Button(frm_bottom, text="変換開始", command=start_proc); btn_start.pack(side=tk.LEFT)
    tk.Label(frm_bottom, text="ログ").pack(anchor="w", padx=2)

    txt_log = tk.Text(root, height=10); txt_log.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 12))
    root.mainloop()
    return 0

# ---------- エントリ ----------
if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
