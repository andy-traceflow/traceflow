"""Phone-number normalization to E.164.

Every phone comparison in the platform (vendor allowlist membership, contact
get-or-create keying, returning-caller matching) must normalize BOTH sides
first. Twilio's `From` already arrives as E.164, but config values (allowlists,
owner numbers) are hand-entered and formatted inconsistently — `+1 (702)
517-8074`, `702-517-8074`, `17025178074` all denote the same line. A raw string
compare silently treats them as different numbers, which at best burns a lookup
and at worst spam-flags a real caller on their second call.

Dependency-free by design: the platform's callers are US/Canada (NANP) surface
contractors and inbound webhook numbers are already E.164, so a full locale
library (phonenumbers) would be weight we don't need. The guarantees are:

  * A valid E.164 string (`+…`) passes through, stripped of formatting.
  * A NANP national/looong number resolves to `+1XXXXXXXXXX` when the default
    region is US/CA.
  * Anything unparseable returns None — the caller decides how to degrade
    (contacts.resolve_contact falls back to the raw value rather than dropping
    a caller; a comparison falls back to a raw compare).

`default_region` comes from the client (ClientConfig.default_phone_region),
never a hardcoded constant at the call site.
"""

from __future__ import annotations

import re

# Region → E.164 country calling code. NANP (US/CA) is what our clients use;
# a few neighbors are included so a mis-set region degrades sensibly. Unknown
# regions can still pass E.164 input through untouched.
_REGION_CALLING_CODE: dict[str, str] = {
    "US": "1",
    "CA": "1",
    "PR": "1",
    "MX": "52",
    "GB": "44",
}

_NON_DIGITS = re.compile(r"\D")

# E.164 allows up to 15 digits; a real subscriber number is at least ~8.
_MIN_E164_DIGITS = 8
_MAX_E164_DIGITS = 15


def normalize(raw: str | None, default_region: str = "US") -> str | None:
    """Return `raw` as an E.164 string, or None if it can't be parsed.

    Idempotent: a value already in E.164 returns unchanged (aside from stripped
    formatting). Never raises.
    """
    if raw is None:
        return None
    s = raw.strip()
    if not s:
        return None

    # Already E.164 (or a `+`-prefixed formatted variant): keep the digits.
    if s.startswith("+"):
        digits = _NON_DIGITS.sub("", s)
        if _MIN_E164_DIGITS <= len(digits) <= _MAX_E164_DIGITS:
            return "+" + digits
        return None

    digits = _NON_DIGITS.sub("", s)
    if not digits:
        return None

    calling_code = _REGION_CALLING_CODE.get(default_region.upper())

    # NANP (calling code "1"): the common inputs are 10-digit national numbers
    # and 11-digit numbers that already include the leading country code.
    if calling_code == "1":
        if len(digits) == 10:
            return "+1" + digits
        if len(digits) == 11 and digits.startswith("1"):
            return "+" + digits
        return None

    # Other known region: assume a national number and prefix its calling code.
    if calling_code and _MIN_E164_DIGITS <= len(calling_code) + len(digits) <= _MAX_E164_DIGITS:
        return "+" + calling_code + digits

    # Unknown region and no `+` — can't infer a country code safely.
    return None
