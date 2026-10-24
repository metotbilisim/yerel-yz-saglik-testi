"""Ortak yardımcılar: Türkçe normalizasyon, tarih/sayı ayrıştırma, sahte kimlik/numara üreticileri, token tahmini.

Üretilen tüm numaralar (TCKN, VKN, IBAN, kart) bilerek sağlama kontrolünden GEÇMEZ.
"""
import hashlib
import math
import re

AYLAR = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım", "aralık"]
AYLAR_BASLIK = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]


def tr_lower(s):
    return s.replace("İ", "i").replace("I", "ı").lower()


def norm_text(s):
    """Küçük harf (TR), tipografik tırnakları sadeleştir, noktalamayı boşluğa çevir, boşlukları tek yap."""
    s = tr_lower(s)
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace(" ", " ")
    s = re.sub(r"[^\w\s%]", " ", s, flags=re.U)
    s = s.replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def compact(s):
    """Yalnızca harf ve rakam, küçük harf (kodları karşılaştırmak için)."""
    return re.sub(r"[^0-9a-zçğıöşü]", "", tr_lower(str(s)))


def fold(s):
    """Türkçe karakterleri ASCII'ye indirger; boşluk ve tireyi alt çizgi yapar (kategori adları için)."""
    t = tr_lower(str(s)).strip()
    for a, b in zip("çğıöşü", "cgiosu"):
        t = t.replace(a, b)
    return re.sub(r"[\s\-]+", "_", t)


_D1 = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
_D2 = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(AYLAR) + r")\s+(\d{4})(?!\d)", re.I)
_D3 = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")


def parse_dates(text):
    """Metindeki tüm tam tarihleri GG.AA.YYYY olarak döndürür (küme)."""
    out = set()
    for m in _D1.finditer(text):
        out.add(f"{int(m.group(1)):02d}.{int(m.group(2)):02d}.{m.group(3)}")
    t = tr_lower(text)
    for m in _D2.finditer(t):
        out.add(f"{int(m.group(1)):02d}.{AYLAR.index(m.group(2)) + 1:02d}.{m.group(3)}")
    for m in _D3.finditer(text):
        out.add(f"{m.group(3)}.{m.group(2)}.{m.group(1)}")
    return out


def norm_date(s):
    """Tek bir tarih dizgesini GG.AA.YYYY'ye çevirir; çevrilemezse None."""
    d = parse_dates(str(s))
    return sorted(d)[0] if d else None


def fmt_long(iso):
    """'12.01.2023' -> '12 Ocak 2023'"""
    g, a, y = iso.split(".")
    return f"{int(g)} {AYLAR_BASLIK[int(a) - 1]} {y}"


# --------------------------------------------------------------------------- sayı biçimleri
_NUM = re.compile(r"-?\d[\d.,]*\d|-?\d")


def parse_tr_number(s):
    """'1.234,56' -> 1234.56; '%18' -> 18.0; '1234,5' -> 1234.5; '1.234' -> 1234.0; '12.5' -> 12.5. Bulunamazsa None.
    Sayı ise doğrudan float döner. Metinde ilk sayı alınır."""
    if isinstance(s, bool):
        return None
    if isinstance(s, (int, float)):
        return float(s)
    m = _NUM.search(str(s))
    return _to_float(m.group(0)) if m else None


def all_tr_numbers(s):
    """Metindeki tüm sayıları (Türkçe biçimden) float listesi olarak döndürür."""
    if isinstance(s, (int, float)) and not isinstance(s, bool):
        return [float(s)]
    return [v for v in (_to_float(m.group(0)) for m in _NUM.finditer(str(s))) if v is not None]


def _to_float(t):
    neg = t.startswith("-")
    t = t.lstrip("-")
    if "." in t and "," in t:
        t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
    elif "," in t:
        t = t.replace(",", ".") if t.count(",") == 1 else t.replace(",", "")
    elif "." in t:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", t):
            t = t.replace(".", "")
        elif t.count(".") > 1:
            t = t.replace(".", "")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def fmt_tr_number(x, nd=2):
    """1234.5 -> '1.234,50'"""
    return f"{x:,.{nd}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def tl(x):
    """1455000 -> '1.455.000,00'"""
    return fmt_tr_number(x, 2)


def wc(text):
    return len(text.split())


def est_tokens(text):
    """Token tahmini: karakter / 3,2 (TASARIM bölüm 5)."""
    return math.ceil(len(text) / 3.2)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


# --------------------------------------------------------------------------- sahte kimlik / numara üreticileri
def tckn_valid(n):
    if len(n) != 11 or not n.isdigit() or n[0] == "0":
        return False
    d = [int(c) for c in n]
    if ((sum(d[0:9:2]) * 7) - sum(d[1:8:2])) % 10 != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def fake_tckn(rng):
    """Sağlama hanesi GEÇERSİZ 11 haneli sahte numara."""
    while True:
        base = [rng.randint(1, 9)] + [rng.randint(0, 9) for _ in range(8)]
        d10 = ((sum(base[0:9:2]) * 7) - sum(base[1:8:2])) % 10
        d11 = (sum(base) + d10) % 10
        d11_bad = (d11 + rng.randint(1, 9)) % 10
        n = "".join(map(str, base)) + str(d10) + str(d11_bad)
        if not tckn_valid(n):
            return n


def vkn_valid(n):
    if len(n) != 10 or not n.isdigit():
        return False
    d = [int(c) for c in n]
    total = 0
    for i in range(9):
        tmp = (d[i] + (9 - i)) % 10
        v = (tmp * (2 ** (9 - i))) % 9
        if tmp != 0 and v == 0:
            v = 9
        total += v
    return (10 - total % 10) % 10 == d[9]


def fake_vkn(rng):
    """10 haneli, sağlama hanesi geçersiz vergi kimlik numarası."""
    while True:
        n = "".join(str(rng.randint(0, 9)) for _ in range(10))
        if not vkn_valid(n):
            return n


def iban_valid(iban):
    s = re.sub(r"\s", "", iban).upper()
    if len(s) < 5:
        return False
    r = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in r)
    return int(digits) % 97 == 1


def fake_iban(rng, spaced=False):
    """TR ile başlayan 26 karakterlik, mod-97 kontrolünden GEÇMEYEN sahte IBAN."""
    while True:
        body = "".join(str(rng.randint(0, 9)) for _ in range(24))
        iban = "TR" + body
        if not iban_valid(iban):
            break
    return fmt_iban(iban) if spaced else iban


def fmt_iban(iban):
    s = re.sub(r"\s", "", iban)
    return " ".join(s[i:i + 4] for i in range(0, len(s), 4))


def luhn_valid(n):
    s = re.sub(r"\D", "", n)
    if not s:
        return False
    total = 0
    for i, c in enumerate(reversed(s)):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def fake_card(rng, spaced=True):
    """16 haneli, Luhn kontrolünden GEÇMEYEN sahte kart numarası."""
    while True:
        n = str(rng.choice([4, 5])) + "".join(str(rng.randint(0, 9)) for _ in range(15))
        if not luhn_valid(n):
            break
    return " ".join(n[i:i + 4] for i in range(0, 16, 4)) if spaced else n


def fake_phone(rng, fmt="spaced"):
    """05xx biçiminde rastgele cep telefonu: '0532 481 27 06'."""
    a = f"05{rng.randint(30, 59)}"
    b, c, d = rng.randint(100, 999), rng.randint(10, 99), rng.randint(10, 99)
    if fmt == "spaced":
        return f"{a} {b} {c} {d}"
    return f"{a}{b}{c}{d}"


_PLATE_LETTERS = "ABCDEFGHJKLMNPRSTUVYZ"


def fake_plate(rng):
    """Gerçekçi biçimli rastgele plaka: '06 BTR 482'."""
    il = rng.randint(1, 81)
    shape = rng.choice([(1, 4), (2, 3), (2, 4), (3, 2), (3, 3)])
    letters = "".join(rng.choice(_PLATE_LETTERS) for _ in range(shape[0]))
    digits = "".join(str(rng.randint(0, 9)) for _ in range(shape[1]))
    if digits[0] == "0":
        digits = str(rng.randint(1, 9)) + digits[1:]
    return f"{il:02d} {letters} {digits}"
