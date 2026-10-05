from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

NUMBER = r"\d+(?:\.\d+)?"
WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫", "01234567890123456789."))
    text = "".join(c for c in text if unicodedata.category(c) != "Cf")
    text = re.sub(r"[\u064b-\u065f\u0670ـ]", "", text)
    text = re.sub(r"(?<=\d)[,،](?=\d{1,2}(?:\D|$))", ".", text)
    text = re.sub(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])", " ", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").replace("ة", "ه").strip().lower()
    return re.sub(r"(كرر|شراء|اشتري|ذهب|دهب|الان)(?=شراء|بيع|ذهب|دهب|الان|من)", r"\1 ", text)


ALIASES = {
    "شراء": ("شراء", "شرا", "اشتري", "اشتر", "اشترا", "نشتري", "الشراء", "للشراء", "buy", "long"),
    "بيع": ("بيع", "بيعوا", "بيعو", "نبيع", "ببيع", "البيع", "للبيع", "sell", "short"),
    "كرر": ("كرر", "كررو", "كرروا", "تكرار", "repeat"),
    "متاحه": ("متاح", "متاحه", "available"),
    "ذهب": ("ذهب", "دهب", "الذهب", "الدهب", "للذهب", "للدهب", "gold"),
    "بيتكوين": ("بيتكوين", "بتكوين", "البيتكوين", "bitcoin"),
    "ستوب": ("ستوب", "استوب", "ستب", "ستوبنا", "وقف", "sl", "stop", "stoploss"),
    "اهداف": ("اهداف", "اهدافنا", "هدفنا", "هدف", "target", "targets", "tp"),
}


def _one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        different = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
        return len(different) <= 1 or (len(different) == 2 and different[1] == different[0] + 1
            and a[different[0]] == b[different[1]] and a[different[1]] == b[different[0]])
    shorter, longer = (a, b) if len(a) < len(b) else (b, a)
    return any(longer[:i] + longer[i + 1:] == shorter for i in range(len(longer)))


@lru_cache(maxsize=1024)
def _word(word: str) -> str:
    exact = {key for key, aliases in ALIASES.items() if word in aliases}
    if exact:
        return next(iter(exact))
    # A single edit in a trading keyword is tolerated; numbers are never corrected.
    if len(word) < 4 or not re.fullmatch(r"[\u0621-\u064a]+", word):
        return word
    matches = {key for key, aliases in ALIASES.items() for alias in aliases if _one_edit(word, alias)}
    return next(iter(matches)) if len(matches) == 1 else word


@dataclass
class ParsedMessage:
    kind: str = "ignored"
    side: str = ""
    entry: str | None = None
    stop: str | None = None
    targets: list[float] = field(default_factory=list)
    instrument: str = ""
    reason: str = ""


def parse_message(raw: str) -> ParsedMessage:
    text = WORDS.sub(lambda m: _word(m[0]), normalize(raw))
    if re.search(r"نحجز\s*ربح|احجز\s*ربح|حجز\s*ربح", text):
        return ParsedMessage("manage")
    sides = {side for word, side in (("شراء", "buy"), ("بيع", "sell")) if re.search(r"\b" + word + r"\b", text)}
    repeated = bool(re.search(r"\b(?:كرر|متاحه)\b|اعاد[هة]\s*(?:دخول|الدخول)", text))
    if len(sides) > 1:
        return ParsedMessage("ambiguous", reason="اتجاه الإشارة ملتبس: تحتوي شراء وبيع")
    side = next(iter(sides), "")
    if re.search(r"\b(?:لا|ممنوع|الغاء|الغ|انتظر|تم|اغلق|اغلاق)\s+(?:ال)?(?:شراء|بيع)\b", text):
        return ParsedMessage()
    instruments = set()
    if re.search(r"\bذهب\b|\bxau(?:usd)?\w*(?:[.#_-]\w*)?", text):
        instruments.add("XAU")
    if re.search(r"\bبيت\s*كوين\b|\bبيتكوين\b|\bbtc(?:usd[t]?)?\w*(?:[.#_-]\w*)?", text):
        instruments.add("BTC")
    for pattern, name in ((r"\bفضه\b|\bsilver\b|\bxag\w*", "XAG"), (r"\bنفط\b|\boil\b|\bbrent\b", "OIL")):
        if re.search(pattern, text):
            instruments.add(name)
    for name in re.findall(r"\b([a-z]{6}(?:[.#_-]\w*)?)\b", text):
        currencies = {"eur", "gbp", "usd", "aud", "nzd", "cad", "chf", "jpy"}
        if name[:3] in currencies and name[3:6] in currencies:
            instruments.add(name[:6].upper())
    if len(instruments) > 1:
        return ParsedMessage("ambiguous", reason="الإشارة تحتوي أكثر من رمز تداول")
    instrument = next(iter(instruments), "")
    target_marker = re.search(r"\bاهداف\b", text)
    stop_marker = re.search(r"\bستوب\b", text)
    next_marker = re.search(r"\b(?:شراء|بيع|ستوب)\b", text[target_marker.end():]) if target_marker else None
    target_end = target_marker.end() + next_marker.start() if next_marker else len(text)
    targets = [float(n) for n in re.findall(NUMBER, text[target_marker.end():target_end]) if float(n) >= 1000] if target_marker else []
    if target_marker and not side and not repeated:
        return ParsedMessage("targets", targets=targets) if targets and not re.search(r"حصاد|صافي|زيادة|اول|نقطه|نقاط|pips", text) else ParsedMessage()
    if not side and not repeated:
        return ParsedMessage()
    stop_matches = list(re.finditer(r"\bستوب\b\s*(?:الخسار[هة]\s*)?(?:عند|من|at)?\s*[:：=@-]?\s*(" + NUMBER + ")", text))
    stops = [m[1] for m in stop_matches]
    if stop_marker and not stops:
        return ParsedMessage("ambiguous", reason="تعذر قراءة سعر الستوب؛ راجع نص الإشارة")
    if len(set(stops)) > 1:
        return ParsedMessage("ambiguous", reason="الإشارة تحتوي أكثر من سعر ستوب")
    excluded = [(m.start(), m.end()) for m in stop_matches]
    if target_marker:
        excluded.append((target_marker.start(), target_end))
    numbers = [m[0] for m in re.finditer(NUMBER, text) if not any(a <= m.start() < b for a, b in excluded)]
    if len(numbers) > 1:
        return ParsedMessage("ambiguous", reason="الإشارة تحتوي عدة أسعار دخول؛ لم يتم اختيار سعر عشوائي")
    if not numbers:
        if repeated:
            return ParsedMessage("repeat", side=side, instrument=instrument, stop=stops[0] if stops else None)
        return ParsedMessage("ambiguous", reason="تعذر قراءة سعر دخول الإشارة")
    if re.search(r"نقطه|نقاط|بيب|\bpips?\b|حصاد|صافي|ربحنا|حققنا", text):
        return ParsedMessage()
    return ParsedMessage("repeat" if repeated else "entry", side, numbers[0], stops[0] if stops else None, targets, instrument)


def expand_price(token: str, current: float, max_distance: float = 25.0) -> float:
    value = float(token)
    if not math.isfinite(current) or current <= 0 or not math.isfinite(value):
        raise ValueError("سعر غير صالح")
    integer_digits = len(token.split(".")[0])
    if integer_digits >= 4:
        return value
    modulus = 10 ** integer_digits
    base = math.floor(current / modulus) * modulus
    candidates = [base + value + delta * modulus for delta in (-1, 0, 1)]
    ranked = sorted(candidates, key=lambda x: abs(x - current))
    if abs(abs(ranked[0] - current) - abs(ranked[1] - current)) < 1e-8:
        raise ValueError("السعر المختصر ملتبس؛ لم يتم التخمين")
    if abs(ranked[0] - current) > max_distance:
        raise ValueError("السعر المختصر بعيد عن السعر الحالي")
    return ranked[0]
