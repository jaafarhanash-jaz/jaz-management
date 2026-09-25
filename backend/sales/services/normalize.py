"""Normalization of lead identity values, for duplicate detection.

Pure functions (no database, no framework) so they are unit-testable in isolation. The results are stored next
to the raw values on `sales_leads` (name_norm, city_norm, phone_norm, ...) so the duplicate lookups are plain
indexed equality checks. The raw values are never altered: what the user typed is what is shown.

Deliberately country-agnostic. Phones are reduced to digits and compared as digit strings - no country code
table, no Iraq/UAE formatting rules, no phone-number library. The one heuristic that lets a national number
("0770 123 4567") meet its international spelling ("+964 770 123 4567") is the digit TAIL: two numbers that
share their last PHONE_TAIL_LEN digits are a *possible* match, while identical digit strings are an exact one.
"""
import re
import unicodedata
from typing import Optional

PHONE_TAIL_LEN = 9      # last N digits used to recognise the same number written with / without a country code
PHONE_MIN_DIGITS = 6    # fewer digits than this is not a phone number worth matching on ("0", "123", ...)

# Arabic harakat, Quranic marks and the superscript alef, plus the tatweel (kashida) elongation character.
_ARABIC_MARK_RANGES = ((0x0610, 0x061A), (0x064B, 0x065F), (0x0670, 0x0670), (0x06D6, 0x06ED), (0x0640, 0x0640))
_ARABIC_MARKS = re.compile("[" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _ARABIC_MARK_RANGES) + "]")
# Letter variants that people type interchangeably; folded so the spellings compare equal.
_ARABIC_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",   # alef forms -> bare alef
    "ى": "ي", "ئ": "ي", "ی": "ي",             # alef maqsura / hamza-on-yeh / Persian yeh -> yeh
    "ؤ": "و",                                  # hamza-on-waw -> waw
    "ة": "ه", "ھ": "ه",                        # teh marbuta / heh doachashmee -> heh
    "ک": "ك",                                  # Persian keheh -> kaf
})
_NON_ALNUM_RUN = re.compile(r"[^\w]+|_+")
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")


def _ascii_digits(value: str) -> str:
    """Every decimal digit of any script (Arabic-Indic, Persian, full-width...) as an ASCII digit; all else dropped."""
    return "".join(str(unicodedata.decimal(ch)) for ch in value if ch.isdecimal())


def digits_only(value: Optional[str]) -> str:
    """Every digit of the value as ASCII (used for phone-number substring search)."""
    return _ascii_digits(unicodedata.normalize("NFKC", str(value))) if value is not None else ""


def norm_text(value: Optional[str]) -> Optional[str]:
    """Business names and cities: case-folded, Arabic letter variants and diacritics folded, digits of any
    script as ASCII, punctuation turned into single spaces. None when nothing is left."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = _ARABIC_MARKS.sub("", text).translate(_ARABIC_FOLD)
    text = "".join(str(unicodedata.decimal(ch)) if ch.isdecimal() else ch for ch in text)
    text = _NON_ALNUM_RUN.sub(" ", text).strip()
    return text or None


def norm_phone(value: Optional[str]) -> Optional[str]:
    """Digits only, with a leading international-call prefix "00" dropped, so "+964 770 123 4567" and
    "00964-770-123-4567" are the same value. None when it has too few digits to identify anyone."""
    if value is None:
        return None
    digits = _ascii_digits(unicodedata.normalize("NFKC", str(value)))
    if digits.startswith("00"):
        digits = digits[2:]
    return digits if len(digits) >= PHONE_MIN_DIGITS else None


def phone_tail(digits: Optional[str]) -> Optional[str]:
    """The last PHONE_TAIL_LEN digits of an already-normalized number, or None when it is too short for a
    tail comparison to mean anything (short numbers are only ever compared whole)."""
    if digits is None or len(digits) < PHONE_TAIL_LEN:
        return None
    return digits[-PHONE_TAIL_LEN:]


def norm_email(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    email = unicodedata.normalize("NFKC", str(value)).strip().lower()
    return email if "@" in email else None


def norm_website(value: Optional[str]) -> Optional[str]:
    """host + path, lower-cased, with the scheme, credentials, port, "www.", query, fragment and trailing
    slashes removed: "https://WWW.Example.com/" -> "example.com", "example.com/en/" -> "example.com/en".
    The path is kept on purpose - many small businesses list a social-media page as their website, and
    "facebook.com/a" and "facebook.com/b" are different businesses. None when there is no plausible host."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip().lower()
    text = _SCHEME.sub("", text)
    end = min((i for i in (text.find("?"), text.find("#")) if i != -1), default=len(text))
    text = text[:end]
    host, _, path = text.partition("/")
    host = host.rsplit("@", 1)[-1]          # user:pass@host
    host = host.split(":", 1)[0]            # :port
    if host.startswith("www."):
        host = host[4:]
    host = host.strip(".")
    if not host or "." not in host or any(ch.isspace() for ch in host):
        return None
    path = "/".join(part for part in path.split("/") if part)
    return f"{host}/{path}" if path else host
