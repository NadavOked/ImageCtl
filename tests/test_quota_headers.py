"""קריאת יתרת המכסה מכותרות התשובה.

‏`usage` בגוף התשובה אומר **כמה צרכתי**; הוא אינו אומר **כמה נשאר**.
‏Groq ו-OpenRouter שולחים את היתרה בכותרת בכל קריאה, ועד כאן זרקנו
אותה.

**הטסט המרכזי כאן הוא ההבחנה בין "אפס" ל"לא דווח"** — שני מצבים
שנראים זהה למי שמקפל אותם, וזה בדיוק הכשל שמתועד ב-
`fleet-measurement-tools-lie`: שישה מקומות שמדווחים אפס כשהם
**נכשלו למדוד**.
"""

from __future__ import annotations

import importlib.util
from email.message import Message
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "call_provider", REPO / "tools" / "agents" / "call-provider.py")
cp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp)


def headers(**pairs) -> Message:
    """אובייקט כותרות כמו זה ש-`urlopen` מחזיר — לא dict.

    ‏`http.client.HTTPMessage` יורש מ-`email.message.Message`, והוא
    **חסר-רגישות לאותיות גדולות**. טסט שמזין `dict` רגיל היה עובר
    בזמן שהקוד נכשל מול ספק ששולח `X-RateLimit-...`.
    """
    msg = Message()
    for key, value in pairs.items():
        msg[key.replace("_", "-")] = value
    return msg


# --- ההבחנה שהכול תלוי בה -----------------------------------------------


def test_no_headers_at_all_is_an_empty_dict():
    """ספק ששותק מחזיר `{}` — ולא אפס."""
    assert cp.quota_from_headers(headers()) == {}


def test_zero_remaining_is_reported_as_zero_and_not_as_silence():
    """‏**"נגמרה המכסה" ו"לא ידוע" הם שני מצבים.**

    בלי הטסט הזה, `if not quota:` היה בולע את שניהם — והכלי היה
    שותק בדיוק ברגע שבו הוא הכי נחוץ.
    """
    got = cp.quota_from_headers(headers(x_ratelimit_remaining_tokens="0"))
    assert got == {"x-ratelimit-remaining-tokens": "0"}
    assert got != {}, "אפס אינו שתיקה"


def test_an_empty_header_value_is_still_a_report():
    """ספק ששולח את הכותרת **ריקה** דיווח משהו — שהוא לא יודע.

    ⚠️ **זה המקרה ש-`if value:` היה בולע**, ו-`if value is not None:`
    תופס. בלי הטסט הזה שתי הצורות נראות זהות, כי `"0"` היא מחרוזת
    לא-ריקה ושתיהן מקבלות אותה. **המבחן היחיד שמפריד ביניהן הוא
    הערך הריק.**

    וההבחנה מעשית: כותרת ריקה פירושה *"הספק שלח שדה ולא מילא
    אותו"* — מצב שונה מ*"הספק לא שלח שדה"*, ושונה מ*"נגמר"*.
    """
    got = cp.quota_from_headers(headers(x_ratelimit_remaining_tokens=""))
    assert got == {"x-ratelimit-remaining-tokens": ""}
    assert got != {}, "כותרת ריקה אינה היעדר כותרת"


def test_a_partial_report_keeps_only_what_arrived():
    """ספק ששולח חלק מהכותרות — לוקחים מה שיש, לא ממציאים את השאר."""
    got = cp.quota_from_headers(
        headers(x_ratelimit_remaining_requests="7"))
    assert got == {"x-ratelimit-remaining-requests": "7"}


# --- מה שהיה נשבר בלי זהירות --------------------------------------------


def test_header_names_are_matched_case_insensitively():
    """ספק ששולח `X-RateLimit-...` נקרא בדיוק כמו אחד ששולח קטן.

    זו הסיבה שהטסט משתמש ב-`Message` ולא ב-`dict`.
    """
    msg = Message()
    msg["X-RateLimit-Remaining-Tokens"] = "1234"
    assert cp.quota_from_headers(msg) == {"x-ratelimit-remaining-tokens": "1234"}


def test_an_unparseable_value_is_kept_verbatim_and_does_not_raise():
    """חלק מהספקים שולחים `1m30s` באיפוס.

    המרה ל-`int` הייתה הופכת בקשה **שהצליחה** לחריגה — כלומר
    הכלי היה מדווח כישלון על מדידה שעבדה.
    """
    got = cp.quota_from_headers(headers(x_ratelimit_reset_tokens="1m30s"))
    assert got == {"x-ratelimit-reset-tokens": "1m30s"}


def test_surrounding_whitespace_is_stripped():
    assert cp.quota_from_headers(
        headers(x_ratelimit_remaining_tokens="  42  ")
    ) == {"x-ratelimit-remaining-tokens": "42"}


# --- שהרשימה עצמה לא תתרוקן בשקט -----------------------------------------


def test_the_watched_header_list_is_not_empty():
    """פונקציה שסורקת רשימה ריקה מחזירה `{}` **תמיד**, ועוברת תמיד.

    זה עיקרון 5 בכלי הבדיקה עצמו.
    """
    assert len(cp.QUOTA_HEADERS) >= 4
    assert all(h.startswith("x-ratelimit-") for h in cp.QUOTA_HEADERS)
    assert all(h == h.lower() for h in cp.QUOTA_HEADERS), (
        "השוואה נעשית מול שם קטן — כותרת ברשימה באותיות גדולות "
        "לא תימצא לעולם")


@pytest.mark.parametrize("name", ["x-ratelimit-remaining-tokens",
                                  "x-ratelimit-remaining-requests"])
def test_each_remaining_header_is_actually_read(name: str):
    """שתי הכותרות שהערך המעשי תלוי בהן, כל אחת בנפרד."""
    assert cp.quota_from_headers(headers(**{name.replace("-", "_"): "5"})) \
        == {name: "5"}
