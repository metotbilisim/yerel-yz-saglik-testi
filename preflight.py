"""Ön kontrol: Ollama erişimi, model varlığı, bağlam ayarı seçimi. (Model yüklenir; yalnızca gerçek koşu öncesi çalıştırılır.)

  python preflight.py <sektor> [--tag full] [--model gemma4:26b]

Adımlar:
 1. Ollama erişimi ve sürüm; model kurulu mu.
 2. Yüklü diğer modeller boşaltılır.
 3. Model num_ctx=65536 ile kısa bir istemle yüklenir; /api/ps'ten size_vram/size okunur. %100 GPU değilse boşaltılır, 49152 denenir.
    O da %100 GPU değilse DURULUR (32768'e sessizce düşülmez).
 4. Seçim sektorler/<s>/results/<tag>/config.json'a yazılır: num_ctx, num_predict_think (65536 -> 28672, 49152 -> 24576), num_predict_nothink=8192,
    ölçülen VRAM, Ollama sürümü, tarih.
 5. En uzun istemin tahmini token'ı + num_predict_think < num_ctx doğrulanır.
"""
import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ortak"))
import ollama_client as oc  # noqa: E402

MODEL = "gemma4:26b"
CTX_PLAN = [(65536, 28672), (49152, 24576)]
NUM_PREDICT_NOTHINK = 8192


def vram_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout.strip()
        return float(out.splitlines()[0])
    except Exception:
        return None


def try_ctx(model, num_ctx):
    """Modeli num_ctx ile yükler; (gpu_yuzde, size_gb, vram_gb, vram_smi_mb, hata) döndürür."""
    r = oc.chat(model, [{"role": "user", "content": "Yalnızca 'tamam' yaz."}], {"num_ctx": num_ctx, "num_predict": 16, "temperature": 0.0}, think=False, timeout=600)
    if r["metrics"]["error"]:
        return None, None, None, None, r["metrics"]["error"]
    time.sleep(1)
    ps = [m for m in oc.loaded_models() if m.get("name") == model or m.get("model") == model]
    if not ps:
        return None, None, None, None, "model /api/ps içinde görünmüyor"
    m = ps[0]
    size, vram = m.get("size", 0), m.get("size_vram", 0)
    pct = round(100 * vram / size, 1) if size else None
    return pct, round(size / 1e9, 2), round(vram / 1e9, 2), vram_used_mb(), None


def run(sektor, tag="full", model=MODEL):
    out_dir = ROOT / "sektorler" / sektor / "results" / tag
    out_dir.mkdir(parents=True, exist_ok=True)
    ver = oc.version()
    if ver is None:
        raise SystemExit("HATA: Ollama'ya ulaşılamıyor (http://localhost:11434). 'ollama serve' çalışıyor mu?")
    if not oc.has_model(model):
        raise SystemExit(f"HATA: {model} yerelde kurulu değil (ollama list).")
    manifest_path = ROOT / "sektorler" / sektor / "manifest.json"
    if not manifest_path.exists():
        raise SystemExit("HATA: manifest.json yok; önce python make_data.py " + sektor)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    longest = max(manifest["gorevler"].items(), key=lambda kv: kv[1]["toplam_token_tahmin"])
    print(f"Ollama {ver}; diğer modeller boşaltılıyor...", flush=True)
    oc.unload_all()
    chosen, trials = None, []
    for num_ctx, np_think in CTX_PLAN:
        print(f"num_ctx={num_ctx} deneniyor...", flush=True)
        pct, size_gb, vram_gb, smi, err = try_ctx(model, num_ctx)
        trials.append({"num_ctx": num_ctx, "gpu_yuzde": pct, "model_boyut_gb": size_gb, "size_vram_gb": vram_gb, "nvidia_smi_mb": smi, "hata": err})
        print("   ->", trials[-1], flush=True)
        if err is None and pct is not None and pct >= 99.9:
            chosen = (num_ctx, np_think, trials[-1])
            break
        oc.unload_all()
    if chosen is None:
        raise SystemExit("HATA: model, 65536 ve 49152 bağlamda %100 GPU'ya sığmadı; 32768'e sessizce düşülmez. Denemeler: " + json.dumps(trials, ensure_ascii=False))
    num_ctx, np_think, t = chosen
    need = longest[1]["toplam_token_tahmin"] + np_think
    if need >= num_ctx:
        raise SystemExit(f"HATA: en uzun istem {longest[0]} ~{longest[1]['toplam_token_tahmin']} token + num_predict_think {np_think} = {need} >= num_ctx {num_ctx}")
    cfg = {"model": model, "sektor": sektor, "num_ctx": num_ctx, "num_predict_think": np_think, "num_predict_nothink": NUM_PREDICT_NOTHINK,
           "vram_size_vram_gb": t["size_vram_gb"], "model_boyut_gb": t["model_boyut_gb"], "gpu_yuzde": t["gpu_yuzde"], "vram_nvidia_smi_mb": t["nvidia_smi_mb"],
           "ollama_surumu": ver, "en_uzun_istem": {"gorev": longest[0], "token_tahmin": longest[1]["toplam_token_tahmin"]},
           "denemeler": trials, "tarih": dt.datetime.now().isoformat(timespec="seconds")}
    (out_dir / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Seçim: num_ctx={num_ctx}, num_predict_think={np_think}. Yazıldı: {out_dir / 'config.json'}")
    return cfg


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("sektor")
    ap.add_argument("--tag", default="full")
    ap.add_argument("--model", default=MODEL)
    a = ap.parse_args()
    run(a.sektor, a.tag, a.model)
