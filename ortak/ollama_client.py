"""Ollama akış istemcisi (hukuk istemcisinin kopyası) + döngü koruması + duvar zaman aşımı.

Döngü koruması: akış sırasında düşünme ve cevap metni AYRI izlenir. Her ~1.500 yeni karakterde, o metnin son 300
karakterlik penceresinin (boşluklar tek boşluğa indirilmiş) son 9.000 karakterde en az 3 kez geçip geçmediğine bakılır.
Geçiyorsa akış kesilir: done_reason="dongu", error=None, o ana kadarki metin kaydedilir.

Zaman aşımı: `timeout` saniyeyi aşan çağrı kesilir (done_reason="zaman_asimi", error="wall timeout ...").
Test için `opener=` ile sahte akış verilebilir (Ollama'ya bağlanılmaz).
"""
import json
import re
import subprocess
import threading
import time
import urllib.request

OLLAMA = "http://localhost:11434"

LOOP_CHECK_EVERY = 1500
LOOP_WINDOW = 300
LOOP_LOOKBACK = 9000
LOOP_MIN_COUNT = 3


def _post(path, payload, timeout=60):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def _get(path):
    with urllib.request.urlopen(OLLAMA + path, timeout=30) as r:
        return json.loads(r.read())


def loaded_models():
    return _get("/api/ps").get("models", [])


def version():
    try:
        return _get("/api/version").get("version")
    except Exception:
        return None


def has_model(name):
    """Model yerelde kurulu mu? (/api/tags)"""
    tags = _get("/api/tags").get("models", [])
    return any(m.get("name") == name or m.get("model") == name for m in tags)


def unload_all():
    for m in loaded_models():
        with _post("/api/generate", {"model": m["name"], "keep_alive": 0}) as r:
            r.read()
    for _ in range(60):
        if not loaded_models():
            return
        time.sleep(1)


def detect_loop(text, window=LOOP_WINDOW, lookback=LOOP_LOOKBACK, min_count=LOOP_MIN_COUNT):
    """Metnin son `window` karakteri (boşluk normalize), son `lookback` karakterde en az `min_count` kez (örtüşmesiz) geçiyor mu?"""
    t = re.sub(r"\s+", " ", text)
    if len(t) < window * min_count:
        return False
    pat = t[-window:]
    return t[-lookback:].count(pat) >= min_count


class LoopWatch:
    """Tek bir akışın (düşünme ya da cevap) döngü denetçisi."""

    def __init__(self):
        self.parts = []
        self.since = 0

    def feed(self, chunk):
        self.parts.append(chunk)
        self.since += len(chunk)
        if self.since < LOOP_CHECK_EVERY:
            return False
        self.since = 0
        text = "".join(self.parts)
        self.parts = [text]
        return detect_loop(text)


class GpuSampler(threading.Thread):
    """nvidia-smi ile VRAM kullanımı ve GPU yükünü örnekler."""

    def __init__(self, interval=0.5):
        super().__init__(daemon=True)
        self.interval = interval
        self.samples = []
        self._halt = threading.Event()

    def run(self):
        while not self._halt.is_set():
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,utilization.gpu,power.draw", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=5).stdout.strip().split(",")
                self.samples.append((float(out[0]), float(out[1]), float(out[2])))
            except Exception:
                pass
            self._halt.wait(self.interval)

    def stop(self):
        self._halt.set()
        self.join(timeout=5)
        if not self.samples:
            return {}
        mem = [s[0] for s in self.samples]
        util = [s[1] for s in self.samples]
        pw = [s[2] for s in self.samples]
        return {"vram_peak_mb": max(mem), "gpu_util_avg": round(sum(util) / len(util), 1),
                "gpu_util_peak": max(util), "power_avg_w": round(sum(pw) / len(pw), 1)}


def classify_error(err):
    """Hata metnini sınıflar: 'zaman_asimi' | 'ag' (bağlantı; yeniden denenebilir) | 'ollama' | None."""
    if not err:
        return None
    e = str(err)
    if "wall timeout" in e or "timed out" in e or "TimeoutError" in e:
        return "zaman_asimi"
    if any(k in e for k in ("URLError", "ConnectionError", "ConnectionReset", "ConnectionRefused", "RemoteDisconnected", "BrokenPipe", "IncompleteRead")):
        return "ag"
    return "ollama"


def chat(model, messages, options, think=True, timeout=1800, keep_alive="30m", tools=None, opener=None):
    """Akışlı sohbet. content, thinking, tool_calls ve metrikleri döndürür.

    opener: test için sahte akış sağlayıcı; çağrı opener(payload, timeout) biçimindedir ve satır dizisi veren bir
    bağlam yöneticisi döndürür. Verilirse GPU örnekleme ve /api/ps sorgusu yapılmaz.
    """
    payload = {"model": model, "messages": messages, "stream": True, "think": think, "options": options, "keep_alive": keep_alive}
    if tools:
        payload["tools"] = tools
    offline = opener is not None
    if opener is None:
        opener = lambda p, t: _post("/api/chat", p, timeout=t)  # noqa: E731
    tool_calls = []
    gpu = None
    if not offline:
        gpu = GpuSampler()
        gpu.start()
    t0 = time.perf_counter()
    t_first_think = t_first_content = t_last_think = None
    think_chunks = content_chunks = 0
    thinking, content = [], []
    final = {}
    error = None
    forced_reason = None
    loop_source = None
    ps_info = None
    watch_think, watch_content = LoopWatch(), LoopWatch()
    try:
        with opener(payload, timeout) as resp:
            for raw in resp:
                if not raw.strip():
                    continue
                msg = json.loads(raw)
                if "error" in msg:
                    error = msg["error"]
                    break
                now = time.perf_counter()
                m = msg.get("message", {})
                if m.get("thinking"):
                    if t_first_think is None:
                        t_first_think = now
                        if not offline:
                            try:
                                ps_info = loaded_models()
                            except Exception:
                                pass
                    t_last_think = now
                    think_chunks += 1
                    thinking.append(m["thinking"])
                    if watch_think.feed(m["thinking"]):
                        forced_reason, loop_source = "dongu", "thinking"
                        break
                if m.get("content"):
                    if t_first_content is None:
                        t_first_content = now
                        if ps_info is None and not offline:
                            try:
                                ps_info = loaded_models()
                            except Exception:
                                pass
                    content_chunks += 1
                    content.append(m["content"])
                    if watch_content.feed(m["content"]):
                        forced_reason, loop_source = "dongu", "content"
                        break
                if m.get("tool_calls"):
                    if t_first_content is None:
                        t_first_content = now
                    tool_calls.extend(m["tool_calls"])
                if now - t0 > timeout:
                    error = f"wall timeout {timeout}s"
                    forced_reason = "zaman_asimi"
                    break
                if msg.get("done"):
                    final = msg
                    break
    except Exception as e:  # ağ / zaman aşımı
        error = f"{type(e).__name__}: {e}"
    t_end = time.perf_counter()
    gpu_stats = gpu.stop() if gpu else {}
    kind = classify_error(error)
    if kind == "zaman_asimi":
        forced_reason = "zaman_asimi"

    ns = 1e9
    load_s = final.get("load_duration", 0) / ns
    pe_count = final.get("prompt_eval_count", 0)
    pe_s = final.get("prompt_eval_duration", 0) / ns
    ev_count = final.get("eval_count", 0)
    ev_s = final.get("eval_duration", 0) / ns
    first_tok = min([t for t in (t_first_think, t_first_content) if t is not None], default=None)

    gpu_pct = None
    if ps_info:
        for p in ps_info:
            if p.get("name", "").split(":")[0] in model or model in p.get("name", ""):
                if p.get("size"):
                    gpu_pct = round(100 * p.get("size_vram", 0) / p["size"], 1)
                    gpu_stats["model_mem_gb"] = round(p["size"] / 1e9, 2)

    thinking_s = None
    if t_first_think is not None:
        end_think = t_first_content if t_first_content is not None else (t_last_think or t_end)
        thinking_s = round(end_think - t_first_think, 2)

    metrics = {
        "model": model,
        "error": error,
        "error_kind": kind,
        "done_reason": forced_reason or final.get("done_reason"),
        "loop_source": loop_source,
        "wall_s": round(t_end - t0, 2),
        "load_s": round(load_s, 2),
        "work_s": round((t_end - t0) - load_s, 2),
        "ttft_s": round(first_tok - t0, 2) if first_tok else None,
        "thinking_s": thinking_s,
        "answer_s": round(t_end - t_first_content, 2) if t_first_content else None,
        "prompt_tokens": pe_count,
        "prompt_tok_per_s": round(pe_count / pe_s, 1) if pe_s else None,
        "eval_tokens": ev_count,
        "thinking_tokens_approx": think_chunks,
        "answer_tokens_approx": content_chunks,
        "gen_tok_per_s": round(ev_count / ev_s, 1) if ev_s else None,
        "gpu_offload_pct": gpu_pct,
        **gpu_stats,
    }
    return {"content": "".join(content), "thinking": "".join(thinking), "tool_calls": tool_calls, "metrics": metrics}
