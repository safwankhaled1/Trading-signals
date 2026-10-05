from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

NUMBER = r"\d+(?:\.\d+)?"


def normalize(text: str) -> str:
    text = text.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫", "0123456789."))
    text = re.sub(r"[\u064b-\u065f\u0670ـ]", "", text)
    return text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").replace("ى", "ي").strip()


@dataclass
class ParsedMessage:
    kind: str = "ignored"
    side: str = ""
    entry: str | None = None
    stop: str | None = None
    targets: list[float] = field(default_factory=list)
    instrument: str = ""


def parse_message(raw: str) -> ParsedMessage:
    text = normalize(raw)
    if re.search(r"نحجز\s*ربح|احجز\s*ربح|حجز\s*ربح", text):
        return ParsedMessage("manage")
    if re.search(r"اهداف|هدفنا", text) and not re.search(r"حصاد|صافي", text):
        targets = [float(n) for n in re.findall(NUMBER, text) if float(n) >= 1000]
        return ParsedMessage("targets", targets=targets) if targets else ParsedMessage()
    if re.fullmatch(r"[\s✅🔥!.,]*متاح[هة]?[\s✅🔥!.,]*", text):
        return ParsedMessage("repeat")
    side = "buy" if re.search(r"اشتري|اشتر\b|شراء|\bbuy\b", text, re.I) else "sell" if re.search(r"بيع|\bsell\b", text, re.I) else ""
    instrument_match = re.search(r"بيت\s*كوين|بتكوين|\bbitcoin\b|\bbtc(?:usd[t]?)?\w*(?:[.#_-]\w*)?|ذهب|دهب|\bxauusd\w*(?:[.#_-]\w*)?|\bgold\b", text, re.I)
    instrument = ""
    if instrument_match:
        instrument = "BTC" if re.search(r"كوين|bitcoin|btc", instrument_match[0], re.I) else "XAU"
    match = re.search(r"\bمن\s*(" + NUMBER + ")", text)
    if not match and instrument_match:
        match = re.match(r"\s*(?:من|عند|at|entry|@|:)?\s*(" + NUMBER + ")", text[instrument_match.end():], re.I)
    stop = re.search(r"(?:ستوب|استوب|وقف(?:\s*الخسار[هة])?|\bsl\b)\s*[:：]?\s*(" + NUMBER + ")", text, re.I)
    if side and match and instrument:
        return ParsedMessage("repeat" if "كرر" in text else "entry", side, match[1], stop[1] if stop else None, instrument=instrument)
    if re.fullmatch(r"\s*كرر[\s!.]*", text):
        return ParsedMessage("repeat")
    return ParsedMessage()


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
