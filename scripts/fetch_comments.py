"""Workshop yorum istatistikleri.

Her mod için Steam'in yorum sayfasını (JSON + HTML parçası) okur, yalnızca
YENİ yorumları sayar ve data/comments.json içine toplu istatistik yazar.

Gizlilik: repo herkese açık olduğundan yorum metni ve yazar adı SAKLANMAZ.
Yinelenenleri ayıklamak için yorumların kısaltılmış hash'i tutulur.

Yalnızca standart kütüphane kullanır (workflow'da pip install gerekmez).

kullanım: python3 scripts/fetch_comments.py [stats.json] [comments.json]
Hata toleranslıdır: bir mod başarısız olursa diğerleri işlenir, çıktı yine yazılır.
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo
from html.parser import HTMLParser

RENDER = "https://steamcommunity.com/comment/PublishedFile_Public/render/{owner}/{feature}/"
ITEM_PAGE = "https://steamcommunity.com/sharedfiles/filedetails/?id={id}"
USER_AGENT = "steam-stats-comments/1.0"

PAGE_SIZE = 50        # istenen sayfa boyutu; Steam farklı dönerse dönen değer kullanılır
MAX_PAGES = 30        # ilk çalıştırmada üst sınır (30 x 50 = 1500 yorum)
SLEEP_SECONDS = 1.0   # istekler arası bekleme
SEEN_LIMIT = 5000     # mod başına saklanan hash sayısı üst sınırı

# Anahtar kelimeler (küçük harfe çevrilmiş metinde, kelime BAŞINDAN eşleşir:
# "bug" -> "bug", "bugs", "buggy" eşleşir; "debug" eşleşmez. "crash" -> "crashes", "crashing").
# COMPLAINT_KEYWORDS günlük raporda uyarı üretir; INFO_KEYWORDS yalnızca bilgi olarak sayılır
# (bunlar "update için teşekkürler" gibi olumlu yorumlarda da geçer).
COMPLAINT_KEYWORDS = [
    "crash", "broken", "bug", "not working", "doesn't work", "doesnt work",
    "error", "freeze", "stuck",
    "isn't working", "isnt working", "does not work", "do not work", "don't work",
    "dont work", "won't work", "wont work", "stopped working", "no longer work",
    "not loading", "won't load",
]
INFO_KEYWORDS = ["patch", "update", "fix"]
KEYWORDS = COMPLAINT_KEYWORDS + INFO_KEYWORDS
_KW_RE = {kw: re.compile(r"\b" + re.escape(kw)) for kw in KEYWORDS}
TR = ZoneInfo("Europe/Istanbul")


# --------------------------------------------------------------------------
# HTML ayrıştırma (stdlib)
# --------------------------------------------------------------------------
class _CommentParser(HTMLParser):
    """Steam yorum HTML'inden (id, zaman damgası, metin) üçlülerini çıkarır."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.comments = []
        self._cur = None
        self._depth = 0          # yorum bloğunun içindeki div derinliği
        self._text_depth = None  # metin bloğunun derinliği
        self._text_parts = []

    @staticmethod
    def _classes(attrs):
        return (dict(attrs).get("class") or "").split()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = self._classes(attrs)
        if tag == "div":
            if self._cur is None and "commentthread_comment" in classes:
                self._cur = {"id": a.get("id") or "", "ts": None, "text": ""}
                self._depth = 1
                return
            if self._cur is not None:
                self._depth += 1
                if "commentthread_comment_text" in classes:
                    self._text_depth = self._depth
                    self._text_parts = []
        if self._cur is not None and "commentthread_comment_timestamp" in classes:
            raw = a.get("data-timestamp")
            if raw and raw.isdigit():
                self._cur["ts"] = int(raw)

    def handle_endtag(self, tag):
        if tag != "div" or self._cur is None:
            return
        if self._text_depth == self._depth:
            self._cur["text"] = " ".join("".join(self._text_parts).split())
            self._text_depth = None
        self._depth -= 1
        if self._depth == 0:
            self.comments.append(self._cur)
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None and self._text_depth is not None:
            self._text_parts.append(data)


def parse_comments(html):
    """HTML parçasından [{'key', 'ts', 'text'}] listesi döner (en yeni önce)."""
    p = _CommentParser()
    p.feed(html or "")
    p.close()
    out = []
    for c in p.comments:
        # Kararlı anahtar: öğe id'si varsa o, yoksa zaman damgası + metin özeti.
        basis = c["id"] or f"{c['ts']}|{c['text']}"
        key = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]
        out.append({"key": key, "ts": c["ts"], "text": c["text"]})
    return out


# --------------------------------------------------------------------------
# Ağ katmanı
# --------------------------------------------------------------------------
def http_get(url, params=None, retries=2):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"istek başarısız: {url} ({last})")


def find_owner(item, get=http_get):
    """Yorum endpoint'i için sahip steamid'si.

    Önce stats.json'daki 'creator' alanı; yoksa mod sayfasındaki yorum
    kurulum kodundan okunur (yedek yol).
    """
    if item.get("creator"):
        return str(item["creator"])
    page = get(ITEM_PAGE.format(id=item["id"]))
    m = re.search(r"PublishedFile_Public[/_](\d{15,20})[/_]", page)
    if not m:
        m = re.search(r'"owner"\s*:\s*"(\d+)"', page)
    if not m:
        raise RuntimeError("yorum sahibi (owner) bulunamadı")
    return m.group(1)


def _int(v, default=None):
    """Steam bazı sayıları metin olarak döndürür ("50"); sayıya çevirir."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def fetch_item_comments(item, known, get=http_get, sleep=time.sleep):
    """Bir modun yeni yorumlarını döner: (yeni_yorumlar, toplam_sayı).

    Yorumlar en yeniden eskiye gelir. Tamamı bilinen ilk sayfada durulur.
    """
    owner = find_owner(item, get)
    url = RENDER.format(owner=owner, feature=item["id"])
    known = set(known)
    fresh, total, start = [], None, 0

    for _ in range(MAX_PAGES):
        data = json.loads(get(url, {"start": start, "count": PAGE_SIZE}))
        if not data.get("success", True):
            raise RuntimeError("Steam success=false döndü")
        total = _int(data.get("total_count"), total)
        page = parse_comments(data.get("comments_html", ""))
        if not page:
            break
        new_here = [c for c in page if c["key"] not in known]
        fresh.extend(new_here)
        if len(new_here) < len(page):
            break  # bilinen yorumlara ulaştık
        step = _int(data.get("pagesize")) or len(page)
        start += step
        if total is not None and start >= total:
            break
        sleep(SLEEP_SECONDS)
    return fresh, total


# --------------------------------------------------------------------------
# İstatistik
# --------------------------------------------------------------------------
def keyword_counts(comments):
    """Her anahtar kelimeyi kaç yorumun içerdiğini sayar."""
    c = Counter()
    for cm in comments:
        t = cm["text"].lower().replace("’", "'")  # kıvrık kesme işareti: doesn’t -> doesn't
        for kw, rx in _KW_RE.items():
            if rx.search(t):
                c[kw] += 1
    return dict(c)


def day_counts(comments):
    """Yorumları İstanbul (TR) takvim gününe göre sayar."""
    c = Counter()
    for cm in comments:
        if cm["ts"]:
            c[datetime.fromtimestamp(cm["ts"], TR).strftime("%Y-%m-%d")] += 1
    return dict(c)


def merge_counts(old, new):
    out = Counter(old or {})
    out.update(new or {})
    return dict(out)


def update_item(prev, item, fresh, total):
    """Önceki durum + yeni yorumlar -> yeni durum (yorum metni saklanmaz)."""
    prev = prev or {}
    seen = (prev.get("seen") or []) + [c["key"] for c in fresh]
    ts = [c["ts"] for c in fresh if c["ts"]]
    return {
        "title": item.get("title"),
        "total": total if total is not None else prev.get("total"),
        "new_since_last": len(fresh),
        "new_keywords": keyword_counts(fresh),
        "keywords_total": merge_counts(prev.get("keywords_total"), keyword_counts(fresh)),
        "daily": merge_counts(prev.get("daily"), day_counts(fresh)),
        "last_comment_ts": max(ts) if ts else prev.get("last_comment_ts"),
        "seen": seen[-SEEN_LIMIT:],
    }


def run(stats, previous, get=http_get, sleep=time.sleep, now=time.time):
    prev_items = (previous or {}).get("items", {})
    items, errors = {}, []
    for item in stats.get("items", []):
        pid = item["id"]
        prev = prev_items.get(pid)
        try:
            fresh, total = fetch_item_comments(
                item, (prev or {}).get("seen", []), get=get, sleep=sleep)
            items[pid] = update_item(prev, item, fresh, total)
        except Exception as e:  # tek mod hatası diğerlerini durdurmasın
            errors.append({"id": pid, "error": str(e)})
            if prev:
                items[pid] = {**prev, "new_since_last": 0, "new_keywords": {}}
        sleep(SLEEP_SECONDS)
    return {"generated_at": int(now()), "items": items, "errors": errors}


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    stats_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "data", "stats.json")
    out_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "..", "data", "comments.json")

    stats = json.load(open(stats_path))
    # Boş/bozuk dosya (ör. elle temizlenmiş) ilk çalıştırma gibi ele alınır.
    try:
        previous = json.load(open(out_path))
        if not isinstance(previous, dict):
            raise ValueError("beklenen JSON nesnesi değil")
    except FileNotFoundError:
        previous = {}
    except ValueError as e:
        print(f"UYARI: {out_path} okunamadı ({e}); sıfırdan başlanıyor", file=sys.stderr)
        previous = {}
    result = run(stats, previous)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(result, open(out_path, "w"), ensure_ascii=False, indent=1)

    new = sum(i["new_since_last"] for i in result["items"].values())
    print(f"comments: {len(result['items'])} mod, {new} yeni yorum, {len(result['errors'])} hata")
    for e in result["errors"]:
        print(f"  HATA {e['id']}: {e['error']}", file=sys.stderr)


if __name__ == "__main__":
    main()
