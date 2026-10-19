"""נתיבי הקוד הפגיעים בהתראות Dependabot של cryptography/pyOpenSSL — אינם בשימוש.

שמונה התראות על ``server/requirements.txt`` נסגרות בטענה אחת: "הקוד שלנו לא
מגיע לנתיב הפגיע" (‏``SECURITY.md``, סעיף "התראות Dependabot על תלויות
מוצמדות"). טענה כזו היא **ציטוט** עד שמישהו בודק אותה — והיא נשברת בשקט ביום
שמישהו מוסיף ``x509.verification`` "כי זה נוח". הטסט הזה הופך אותה לראיה:
הוא קורא את עץ ``server/`` ונופל כשנתיב מהרשימה מופיע, ואז מי שהוסיף אותו
חייב לחזור להתראה ולהחליט.

| התראה | נתיב פגיע | מה נאסר כאן |
|---|---|---|
| cryptography CVE-2026-69249 | בניית שרשרת ב-``x509.verification`` | כל שימוש במודול |
| cryptography CVE-2026-34073 | name constraints ב-``x509.verification`` | כל שימוש במודול |
| cryptography CVE-2026-26007 | נקודות על עקומות SECT | ‏``ec.SECT*`` |
| pyOpenSSL CVE-2026-27459 | ‏``set_cookie_generate_callback`` (DTLS) | הקריאה לה |
| pyOpenSSL CVE-2026-27448 | ‏``set_tlsext_servername_callback`` | הקריאה לה |

התראות ה-OpenSSL שבתוך ה-wheels (‏GHSA-537c, ‏CVE-2024-12797 ועוד) אינן כאן:
הן לא עניין של קוד אלא של **מאיפה הספרייה מגיעה** — בשרת היא ``apt``
שמקשר ל-libssl של המערכת (‏``Depends: libssl3t64``).

מגבלה, במפורש: הסריקה היא AST על קוד פייתון ב-``server/``. שימוש דינמי
(‏``importlib``, ‏``getattr`` עם מחרוזת) אינו נתפס, וקוד מחוץ ל-``server/``
לא נסרק — ‏``agent/`` הוא sh, ו-``tools/`` אינו רץ בשרת הייצור.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"

#: שמות ששימוש בהם (קריאה, ייבוא, גישה לתכונה) פותח את אחד הנתיבים הפגיעים.
FORBIDDEN_NAMES = {
    "set_cookie_generate_callback": "pyOpenSSL CVE-2026-27459 (DTLS cookie)",
    "set_cookie_verify_callback": "pyOpenSSL CVE-2026-27459 (DTLS cookie)",
    "set_tlsext_servername_callback": "pyOpenSSL CVE-2026-27448 (servername)",
    "set_servername_callback": "pyOpenSSL CVE-2026-27448 (servername)",
    "PolicyBuilder": "cryptography CVE-2026-69249/34073 (x509.verification)",
    "ClientVerifier": "cryptography CVE-2026-69249/34073 (x509.verification)",
    "ServerVerifier": "cryptography CVE-2026-69249/34073 (x509.verification)",
}
SECT_CURVE = re.compile(r"^SECT\d+[KR]\d$")
VERIFICATION_MODULE = "cryptography.x509.verification"


def find_forbidden(source: str) -> list[str]:
    """שורות ``<שורה>: <סיבה>`` לכל שימוש אסור במקור פייתון אחד."""
    hits: list[str] = []
    for node in ast.walk(ast.parse(source)):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(VERIFICATION_MODULE):
                    hits.append(f"{line}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(VERIFICATION_MODULE):
                hits.append(f"{line}: from {module} import ...")
            if module == "cryptography.x509" and any(
                    a.name == "verification" for a in node.names):
                hits.append(f"{line}: from cryptography.x509 import verification")
        names: list[str] = []
        if isinstance(node, ast.Attribute):
            names.append(node.attr)
        elif isinstance(node, ast.Name):
            names.append(node.id)
        elif isinstance(node, ast.ImportFrom):
            names += [a.name for a in node.names]
        for name in names:
            if name in FORBIDDEN_NAMES:
                hits.append(f"{line}: {name} — {FORBIDDEN_NAMES[name]}")
            elif SECT_CURVE.match(name):
                hits.append(f"{line}: {name} — cryptography CVE-2026-26007 (SECT)")
    return hits


def server_sources() -> list[Path]:
    # ``static/`` הוא vendor של JS — אין שם פייתון, והסריקה לא צריכה לעבור עליו.
    return [p for p in sorted(SERVER.rglob("*.py")) if "static" not in p.parts]


def test_scan_actually_covers_the_server_tree() -> None:
    """סריקה שלא קראה דבר "עוברת" — עיקרון 5. מוודאים שהיא ראתה את הקבצים
    שמשתמשים בספריות הנבדקות, ולא רק שלא מצאה כלום."""
    names = {p.name for p in server_sources()}
    assert len(names) > 20, f"הסריקה ראתה רק {len(names)} קבצים"
    for must in ("interserver_auth.py", "interserver_api.py", "console_tls.py"):
        assert must in names, f"{must} לא נסרק (יש: {sorted(names)[:10]}...)"


def test_server_code_does_not_use_the_vulnerable_paths() -> None:
    offenders: dict[str, list[str]] = {}
    for path in server_sources():
        hits = find_forbidden(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(REPO))] = hits
    assert not offenders, (
        "נתיב פגיע מהתראות Dependabot נכנס לקוד השרת — חוזרים להתראה "
        f"(SECURITY.md) ומחליטים לפני שממשיכים: {offenders}")


def test_the_only_curve_we_generate_is_p256() -> None:
    """ראיה חיובית לצד השלילית: מה שכן בשימוש הוא SECP256R1 (לא SECT)."""
    text = (SERVER / "interserver_auth.py").read_text(encoding="utf-8")
    assert "ec.generate_private_key(ec.SECP256R1())" in text


def test_scanner_catches_each_forbidden_path() -> None:
    """בקרה שלילית של הסורק עצמו: על קוד שמכיל כל נתיב — הוא חייב לצעוק.
    בלי זה, ‏``find_forbidden`` שמחזיר תמיד ``[]`` היה "מוכיח" הכול."""
    cases = {
        "import cryptography.x509.verification": "verification",
        "from cryptography.x509.verification import PolicyBuilder": "verification",
        "from cryptography.x509 import verification": "verification",
        "from cryptography.x509 import verification as v\nv.PolicyBuilder()": "PolicyBuilder",
        "ctx.set_cookie_generate_callback(cb)": "set_cookie_generate_callback",
        "ctx.set_tlsext_servername_callback(cb)": "set_tlsext_servername_callback",
        "from cryptography.hazmat.primitives.asymmetric import ec\n"
        "ec.generate_private_key(ec.SECT571R1())": "SECT571R1",
    }
    for source, needle in cases.items():
        hits = find_forbidden(source)
        assert hits and any(needle in h for h in hits), (source, hits)


def test_scanner_is_quiet_on_what_we_do_use() -> None:
    clean = (
        "from cryptography import x509\n"
        "from cryptography.x509.oid import NameOID\n"
        "from cryptography.hazmat.primitives.asymmetric import ec\n"
        "from OpenSSL import SSL\n"
        "key = ec.generate_private_key(ec.SECP256R1())\n"
        "ctx = SSL.Context(SSL.TLS_METHOD)\n"
        "ctx.set_verify(SSL.VERIFY_PEER, cb)\n"
    )
    assert find_forbidden(clean) == []
