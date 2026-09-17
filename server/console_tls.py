"""‏TLS לקונסולה (‏#703, tracer 5).

הקונסולה (8081, כרטיס הניהול) הגישה HTTP וסיסמת admin עברה גלויה —
במכללה שמלמדת סייבר. מ-tracer 5 המאזין של הקונסולה הוא **HTTPS בלבד**:
תעודה חתומה-עצמית שנוצרת בהפעלה הראשונה ל-``<data_dir>/console-tls/``
(‏``console.crt`` + ‏``console.key`` ב-0600) ונשארת **אותה תעודה** בכל
הפעלה — הדפדפן של המפעיל מאשר אותה פעם אחת. ‏HTTP על 8081 אינו מוגש
(fail-closed): אין הפניה שקטה ואין מאזין-מפנה, כי מאזין HTTP על אותו
פורט הוא בדיוק מה שהדפדפן (או תלמיד) היה נופל אליו קודם.

**למה תעודה נפרדת מזהות ה-interserver (`storage_nodes.ensure_identity`)
ולא אותה זהות:** ה-SPKI של זהות ה-interserver **מוצמד** על ידי הצד
השני (SPKI-pin, #740), והיא נוצרת בלי SAN של כרטיס הניהול (על ראשי —
בלי SAN כלל, כתעודת-לקוח). תעודת הקונסולה צריכה SAN של ‎--console-host‏
כדי שהדפדפן יתלונן על "לא מוכרת" בלבד ולא גם על "שם לא תואם", ואסור
שחידושה ישבור pin. המחולל **משותף** (`interserver_auth.generate_self_signed`)
— אין כאן מחולל שני, רק זהות שנייה.

**למה stdlib ssl ולא pyOpenSSL:** ‏uvicorn מקבל ``ssl_certfile``/
``ssl_keyfile`` ובונה ``ssl.SSLContext`` בעצמו; לקונסולה אין תעודת-לקוח
ואין exporter, ולכן אין צורך ב-terminator של pyOpenSSL כמו ב-8443.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import stat as stat_module
from dataclasses import dataclass
from pathlib import Path

from .interserver_auth import (generate_self_signed, is_loopback,
                               write_credential_0600)

#: תת-התיקייה של ``data_dir`` שבה חיה זהות הקונסולה.
CONSOLE_TLS_DIRNAME = "console-tls"
CERT_NAME = "console.crt"
KEY_NAME = "console.key"
#: ה-CN של התעודה; מה שהדפדפן מציג ב"הונפק ל".
COMMON_NAME = "imagectl-console"


class ConsoleTLSError(RuntimeError):
    """תצורת TLS של הקונסולה שאינה מתקבלת — נאמרת בשמה, לא מתוקנת בשקט."""


@dataclass(frozen=True)
class ConsoleTLS:
    """זהות הקונסולה כפי שנטענה/נוצרה: הנתיבים ל-uvicorn וטביעת האצבע למפעיל."""

    cert_path: Path
    key_path: Path
    #: ‏SHA-256 של התעודה (DER), ``AB:CD:…`` — הצורה שהדפדפן מציג
    #: ב"פרטי תעודה", כדי שנדב ישווה ביד.
    fingerprint_sha256: str
    #: ה-SANs שבתעודה, כפי שנקראו ממנה (לא כפי שביקשנו).
    sans: tuple[str, ...]

    def public_info(self) -> dict:
        """מה שהקונסולה מקבלת ב-``/me`` לשורת הסטטוס."""
        return {"mode": "self-signed", "fingerprint_sha256": self.fingerprint_sha256}

    def uvicorn_kwargs(self) -> dict:
        """הארגומנטים ל-``uvicorn.Config`` של מאזין הקונסולה."""
        return {"ssl_certfile": str(self.cert_path), "ssl_keyfile": str(self.key_path)}


def check_tls_flag(mode: str, console_host: str) -> None:
    """‏``--console-tls off`` מותר **רק** על loopback (הרצת פיתוח/e2e).

    על כל כתובת אחרת הסיסמה הייתה עוברת ברשת גלויה — וזה בדיוק מה
    ש-tracer 5 סוגר. הסירוב נוקב בשני הדגלים, לא נופל ל-TLS בשקט."""
    if mode == "off" and not is_loopback(console_host):
        raise ConsoleTLSError(
            f"--console-tls off מותר רק עם --console-host על loopback "
            f"(127.0.0.1); התקבל --console-host {console_host!r}. הקונסולה "
            f"על כרטיס ניהול מוגשת ב-HTTPS בלבד (#703)")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def fingerprint_sha256(cert_pem: bytes) -> str:
    """‏SHA-256 של התעודה (DER), ‏``AB:CD:…`` — כמו בדפדפן."""
    from cryptography import x509
    der = x509.load_pem_x509_certificate(cert_pem).public_bytes(_encoding_der())
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def _encoding_der():
    from cryptography.hazmat.primitives.serialization import Encoding
    return Encoding.DER


def cert_sans(cert_pem: bytes) -> tuple[str, ...]:
    """ה-SANs שבתעודה — IP ו-DNS — כמחרוזות, כפי שנכתבו בה."""
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(cert_pem)
    try:
        ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return ()
    values = [str(v) for v in ext.value.get_values_for_type(x509.IPAddress)]
    values += ext.value.get_values_for_type(x509.DNSName)
    return tuple(values)


def ensure_console_cert(data_dir, console_host: str,
                        hostname: str | None = None) -> ConsoleTLS:
    """מחזיר את זהות הקונסולה — יוצר אותה **פעם אחת** אם אין.

    ‏SAN = ‏``console_host`` (IP או DNS) + שם המארח. תעודה קיימת נטענת כמות
    שהיא (גם אם ``console_host`` השתנה מאז — היא לא מתחלפת מאחורי הגב
    של הדפדפן שכבר אישר אותה; מדפיסים אזהרה ב-``main``, לא מחליפים).
    חצי זהות (תעודה בלי מפתח או להפך) היא כשל בשמו, לא יצירה מחדש
    שקטה שהייתה מחליפה תעודה שדפדפנים כבר מכירים (עיקרון 5).
    """
    directory = Path(data_dir) / CONSOLE_TLS_DIRNAME
    cert_path, key_path = directory / CERT_NAME, directory / KEY_NAME
    have_cert, have_key = cert_path.exists(), key_path.exists()
    if have_cert != have_key:
        raise ConsoleTLSError(
            f"זהות הקונסולה חלקית ב-{directory}: "
            f"{'תעודה בלי מפתח' if have_cert else 'מפתח בלי תעודה'} — "
            f"הסר את שני הקבצים כדי שתיווצר זהות חדשה")
    if not have_cert:
        directory.mkdir(parents=True, exist_ok=True)
        hostname = hostname or socket.gethostname()
        ip_sans = [console_host] if _is_ip(console_host) else []
        dns_sans = [] if _is_ip(console_host) else [console_host]
        if hostname and hostname not in dns_sans:
            dns_sans.append(hostname)
        cert_pem, key_pem = generate_self_signed(
            COMMON_NAME, ip_sans=ip_sans, dns_sans=dns_sans)
        write_credential_0600(key_path, key_pem.decode())
        cert_path.write_bytes(cert_pem)
    elif os.name == "posix":
        # המפתח הוא שלנו — מצב רחב מדי (umask, העתקה ביד) מוחזר ל-0600.
        mode = stat_module.S_IMODE(os.stat(key_path).st_mode)
        if mode != 0o600:
            os.chmod(key_path, 0o600)
    cert_pem = cert_path.read_bytes()
    return ConsoleTLS(cert_path=cert_path, key_path=key_path,
                      fingerprint_sha256=fingerprint_sha256(cert_pem),
                      sans=cert_sans(cert_pem))
