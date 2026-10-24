"""ollama_client birim testi: sahte akış üreteciyle döngü koruması ve zaman aşımı. Ollama'ya bağlanmaz."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "ortak"))
import ollama_client as oc  # noqa: E402


class FakeStream:
    def __init__(self, msgs, delay=None):
        self.msgs = msgs
        self.delay = delay

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        import time
        for m in self.msgs:
            if self.delay:
                time.sleep(self.delay)
            yield (json.dumps(m) + "\n").encode()


def _opener(msgs, delay=None):
    return lambda payload, timeout: FakeStream(msgs, delay)


def _done(reason="stop"):
    return {"done": True, "done_reason": reason, "eval_count": 10, "prompt_eval_count": 5, "eval_duration": 10 ** 9, "prompt_eval_duration": 10 ** 9}


def run():
    # 1) normal akış: stop
    normal = [{"message": {"thinking": f"Adım {i}: farklı bir düşünce parçası, içerik {i * 7919 % 1013}. "}} for i in range(120)]
    normal += [{"message": {"content": f"Cevap parçası {i} "}} for i in range(30)] + [_done()]
    r = oc.chat("m", [{"role": "user", "content": "x"}], {}, opener=_opener(normal))
    assert r["metrics"]["done_reason"] == "stop" and r["metrics"]["error"] is None, r["metrics"]
    assert r["content"].startswith("Cevap parçası 0")

    # 2) düşünme döngüsü: aynı 100 karakterlik cümle tekrar tekrar
    sentence = "Bir kez daha kontrol edeyim: etiketler tutarlı mı, her kişi aynı numarayı alıyor mu, evet sanırım ama yine bakayım. "
    loop = [{"message": {"thinking": "Önce metni okuyorum ve varlıkları çıkarıyorum. " * 3}}]
    loop += [{"message": {"thinking": sentence}} for _ in range(400)] + [_done("stop")]
    r = oc.chat("m", [{"role": "user", "content": "x"}], {}, opener=_opener(loop))
    m = r["metrics"]
    assert m["done_reason"] == "dongu" and m["error"] is None and m["loop_source"] == "thinking", m
    assert len(r["thinking"]) < 400 * len(sentence), "akış kesilmedi"
    assert r["thinking"].count("Bir kez daha kontrol") >= 3, "kesilen metin kaydedilmedi"

    # 3) cevap döngüsü
    row = '{"id": "M01", "kategori": "randevu", "birim": "poliklinik_randevu", "acil": false},\n'
    loop2 = [{"message": {"content": row}} for _ in range(300)] + [_done("stop")]
    r = oc.chat("m", [{"role": "user", "content": "x"}], {}, opener=_opener(loop2))
    assert r["metrics"]["done_reason"] == "dongu" and r["metrics"]["loop_source"] == "content", r["metrics"]
    assert r["content"]

    # 4) yinelenen ama kısa ortak ifadeler döngü sayılmaz (JSON listesi, her satır farklı kimlik)
    rows = [{"message": {"content": '{"id": "M%03d", "kategori": "randevu", "birim": "poliklinik_randevu", "acil": false, "not": "mesaj %d için %d"},\n' % (i, i, i * 31)}} for i in range(120)]
    r = oc.chat("m", [{"role": "user", "content": "x"}], {}, opener=_opener(rows + [_done()]))
    assert r["metrics"]["done_reason"] == "stop", r["metrics"]

    # 5) duvar zaman aşımı
    slow = [{"message": {"thinking": f"parça {i} "}} for i in range(50)] + [_done()]
    r = oc.chat("m", [{"role": "user", "content": "x"}], {}, timeout=0.3, opener=_opener(slow, delay=0.1))
    m = r["metrics"]
    assert m["done_reason"] == "zaman_asimi" and m["error"] and m["error_kind"] == "zaman_asimi", m

    # 6) detect_loop doğrudan
    assert not oc.detect_loop("kısa metin")
    assert oc.detect_loop(("a" * 50 + " b" * 100 + " ") * 40)
    assert not oc.detect_loop(" ".join(f"kelime{i}" for i in range(3000)))
    print("ollama_client birim testi: GEÇTİ (normal=stop, düşünme/cevap döngüsü=dongu, zaman aşımı=zaman_asimi)")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    run()
