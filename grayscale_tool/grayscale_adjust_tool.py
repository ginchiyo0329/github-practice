# grayscale_adjust_tool.py
# 調整つきグレースケール変換ツール（Pillowのみ）
# 出力先：ユーザーのデスクトップ\outputfolder（必ずここ）
# 仕様：.py / .exe どちらでも同じ挙動。EXIF/ICCは付与しない（サムネも完全グレー）

from __future__ import annotations
import os
import sys
import time
import subprocess
from typing import Iterable, List, Tuple, Dict, Any

from PIL import Image, ImageOps, ImageEnhance, UnidentifiedImageError  # pip install pillow

# 対応拡張子
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif"}

# JPEG保存設定（EXIF/ICCは渡さない）
JPEG_SAVE_KW = dict(
    quality=95,
    subsampling=0,
    optimize=True,
    progressive=False,
)

# ---------- 出力先（常にDesktop\outputfolder） ----------
def get_output_dir() -> str:
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out = os.path.join(desktop, "outputfolder")
    os.makedirs(out, exist_ok=True)
    return out

OUTPUT_DIR = get_output_dir()  # 起動時に作成

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
                        seen.add(fp); paths.append(fp)
        elif os.path.isfile(p) and is_image_file(p):
            if p not in seen:
                seen.add(p); paths.append(p)
    paths.sort()
    return paths

def safe_out_path(filename: str) -> str:
    name, ext = os.path.splitext(filename)
    dst = os.path.join(OUTPUT_DIR, f"{name}_adj{ext}")
    n = 2
    while os.path.exists(dst):
        dst = os.path.join(OUTPUT_DIR, f"{name}_adj-{n}{ext}")
        n += 1
    return dst

# ---------- 調整処理 ----------
def apply_adjustments(gray: Image.Image, cfg: Dict[str, Any]) -> Image.Image:
    """
    gray: Lモードの画像を想定。cfgで調整。
    cfg = {
      "autocontrast": bool,
      "cutoff": int (0-5),
      "brightness": float (0.5-1.5),
      "contrast": float (0.5-1.5),
      "gamma": float (0.7-1.3)
    }
    """
    im = gray

    # 1) オートコントラスト
    if cfg.get("autocontrast", False):
        cutoff = int(cfg.get("cutoff", 1))
        im = ImageOps.autocontrast(im, cutoff=cutoff)

    # 2) 明るさ
    b = float(cfg.get("brightness", 1.0))
    if abs(b - 1.0) > 1e-6:
        im = ImageEnhance.Brightness(im).enhance(b)

    # 3) コントラスト
    c = float(cfg.get("contrast", 1.0))
    if abs(c - 1.0) > 1e-6:
        im = ImageEnhance.Contrast(im).enhance(c)

    # 4) ガンマ補正（LUT）
    g = float(cfg.get("gamma", 1.0))
    if abs(g - 1.0) > 1e-6:
        inv = 1.0 / max(g, 1e-6)
        lut = [int(round((i / 255.0) ** inv * 255.0)) for i in range(256)]
        im = im.point(lut, mode="L")

    return im

def convert_one(src: str, cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """
    1枚処理。成功: (True, 出力パス) / 失敗: (False, 理由)
    """
    try:
        with Image.open(src) as im:
            if getattr(im, "is_animated", False):
                return False, "アニメGIFのためスキップ"

            # L化（確実に1chへ）
            if im.mode not in ("L", "LA"):
                im = im.convert("RGB")
            gray = ImageOps.grayscale(im)

            # 調整
            out_img = apply_adjustments(gray, cfg)

            # 保存先・保存
            base = os.path.basename(src)
            dst = safe_out_path(base)

            ext = os.path.splitext(dst)[1].lower()
            save_kw = {}
            if ext in (".jpg", ".jpeg"):
                save_kw.update(JPEG_SAVE_KW)

            out_img.save(dst, **save_kw)
            return True, dst

    except UnidentifiedImageError:
        return False, "不正な画像/未対応の形式"
    except Exception as e:
        return False, f"失敗: {e}"

# ---------- 実行ラッパ ----------
def default_cfg() -> Dict[str, Any]:
    return {
        "autocontrast": False,
        "cutoff": 1,
        "brightness": 1.0,
        "contrast": 1.0,
        "gamma": 1.0,
    }

def run_cli(argv: List[str]) -> int:
    targets = collect_targets(argv)
    if not targets:
        return launch_gui()
    return process_targets(targets, default_cfg())

def process_targets(targets: List[str], cfg: Dict[str, Any]) -> int:
    ok = skip = 0
    start = time.time()
    for i, p in enumerate(targets, 1):
        res, info = convert_one(p, cfg)
        if res:
            ok += 1; print(f"[{i}/{len(targets)}] OK  : {os.path.basename(p)} -> {os.path.basename(info)}")
        else:
            skip += 1; print(f"[{i}/{len(targets)}] SKIP: {os.path.basename(p)} ({info})")
    dur = time.time() - start
    print("-"*60)
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

    cfg = default_cfg()
    targets: List[str] = []

    def add_files():
        files = filedialog.askopenfilenames(
            title="画像ファイルを選択",
            filetypes=[("画像", "*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.bmp;*.gif"), ("すべて", "*.*")]
        ); _push(list(files))

    def add_folder():
        d = filedialog.askdirectory(title="フォルダを選択")
        if d: _push([d])

    def _push(items: List[str]):
        nonlocal targets
        new = collect_targets(items)
        exist = set(targets)
        added = [p for p in new if p not in exist]
        if not added: return
        targets += added
        for p in added: lb.insert(tk.END, p)
        lbl_count.set(f"{len(targets)} 件")

    def clear_list():
        targets.clear(); lb.delete(0, tk.END); lbl_count.set("0 件")

    def start_proc():
        if not targets:
            messagebox.showwarning("警告", "処理対象がありません。ファイルまたはフォルダを追加してください。"); return
        # cfg をUIから取得
        cfg["autocontrast"] = bool(var_ac.get())
        cfg["cutoff"] = int(spin_cutoff.get())
        cfg["brightness"] = float(scale_b.get())
        cfg["contrast"] = float(scale_c.get())
        cfg["gamma"] = float(scale_g.get())

        btn_start.config(state=tk.DISABLED); root.update_idletasks()
        ok = skip = 0; start = time.time()
        for i, p in enumerate(targets, 1):
            res, info = convert_one(p, cfg)
            if res: ok += 1; log(f"[{i}/{len(targets)}] OK  : {os.path.basename(p)} → {os.path.basename(info)}")
            else:   skip += 1; log(f"[{i}/{len(targets)}] SKIP: {os.path.basename(p)} （{info}）")
            root.update()

        dur = time.time() - start
        messagebox.showinfo(
            "完了",
            f"変換が完了しました。\n成功: {ok} / スキップ: {skip}\n所要時間: {dur:.2f} 秒\n出力先: {OUTPUT_DIR}"
        )
        try: subprocess.Popen(f'explorer "{OUTPUT_DIR}"')
        except Exception: pass
        btn_start.config(state=tk.NORMAL)

    def log(msg: str):
        txt_log.insert(tk.END, msg + "\n"); txt_log.see(tk.END)

    # --- UI ---
    root = tk.Tk()
    root.title("グレースケール調整ツール（Pillow・Desktop/outputfolder）")
    root.geometry("860x620")

    # 上段：ファイルリスト
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

    # 右側：調整パネル
    panel = tk.LabelFrame(frm_mid, text="調整", padx=10, pady=10)
    panel.pack(side=tk.LEFT, fill=tk.Y, padx=10)

    var_ac = tk.IntVar(value=0)
    tk.Checkbutton(panel, text="オートコントラスト", variable=var_ac).grid(row=0, column=0, sticky="w")
    tk.Label(panel, text="cutoff（0-5）").grid(row=1, column=0, sticky="w")
    spin_cutoff = tk.Spinbox(panel, from_=0, to=5, width=5)
    spin_cutoff.delete(0, "end"); spin_cutoff.insert(0, "1")
    spin_cutoff.grid(row=1, column=1, sticky="w", pady=2)

    tk.Label(panel, text="明るさ 0.50–1.50").grid(row=2, column=0, sticky="w", pady=(8,0))
    scale_b = tk.Scale(panel, from_=0.50, to=1.50, resolution=0.01, orient="horizontal", length=220)
    scale_b.set(1.00); scale_b.grid(row=2, column=1, padx=4)

    tk.Label(panel, text="コントラスト 0.50–1.50").grid(row=3, column=0, sticky="w", pady=(8,0))
    scale_c = tk.Scale(panel, from_=0.50, to=1.50, resolution=0.01, orient="horizontal", length=220)
    scale_c.set(1.00); scale_c.grid(row=3, column=1, padx=4)

    tk.Label(panel, text="ガンマ 0.70–1.30").grid(row=4, column=0, sticky="w", pady=(8,0))
    scale_g = tk.Scale(panel, from_=0.70, to=1.30, resolution=0.01, orient="horizontal", length=220)
    scale_g.set(1.00); scale_g.grid(row=4, column=1, padx=4)

    # 下段：開始＆ログ
    frm_bottom = tk.Frame(root); frm_bottom.pack(fill=tk.BOTH, expand=False, padx=12, pady=8)
    btn_start = tk.Button(frm_bottom, text="変換開始", command=start_proc); btn_start.pack(side=tk.LEFT)
    tk.Label(frm_bottom, text="ログ").pack(anchor="w", padx=8)

    txt_log = tk.Text(root, height=10); txt_log.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0,12))
    root.mainloop()
    return 0

# ---------- エントリ ----------
if __name__ == "__main__":
    sys.exit(run_cli(sys.argv[1:]))
