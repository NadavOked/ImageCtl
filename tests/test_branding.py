"""לוגו המוסד — העלאה, החלפה, הסרה.

הקובץ יושב בתיקיית הנתונים, ולכן הבדיקות מוודאות שהוא באמת נכתב לשם
ושהחלפה לא משאירה שני לוגואים.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100ffff03000006000557bfabd400"
    "00000049454e44ae426082"
)
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'


def branding_dir(server):
    from pathlib import Path
    return Path(server["app"].state.data_dir) / "branding"


def test_no_logo_answers_204(server):
    assert server["anon"].get("/api/console/branding/logo").status_code == 204


def test_upload_then_serve(server):
    admin = server["admin"]
    r = admin.post("/api/console/branding/logo", content=PNG,
                   headers={"Content-Type": "image/png"})
    assert r.status_code == 200

    served = server["anon"].get("/api/console/branding/logo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG
    assert (branding_dir(server) / "logo.png").is_file()


def test_the_login_screen_can_fetch_it_without_signing_in(server):
    server["admin"].post("/api/console/branding/logo", content=PNG,
                         headers={"Content-Type": "image/png"})
    # מסך הכניסה מציג את הלוגו לפני שיש cookie.
    assert server["anon"].get("/api/console/branding/logo").status_code == 200


def test_replacing_leaves_exactly_one_file(server):
    admin = server["admin"]
    admin.post("/api/console/branding/logo", content=PNG,
               headers={"Content-Type": "image/png"})
    admin.post("/api/console/branding/logo", content=SVG,
               headers={"Content-Type": "image/svg+xml"})
    files = sorted(p.name for p in branding_dir(server).iterdir())
    assert files == ["logo.svg"]
    assert server["anon"].get(
        "/api/console/branding/logo").headers["content-type"] == "image/svg+xml"


def test_removing_restores_the_default(server):
    admin = server["admin"]
    admin.post("/api/console/branding/logo", content=PNG,
               headers={"Content-Type": "image/png"})
    assert admin.delete("/api/console/branding/logo").status_code == 200
    assert server["anon"].get("/api/console/branding/logo").status_code == 204


@pytest.mark.parametrize("media", ["application/pdf", "text/html", "application/zip", ""])
def test_only_image_types_are_accepted(server, media):
    r = server["admin"].post("/api/console/branding/logo", content=PNG,
                             headers={"Content-Type": media} if media else {})
    assert r.status_code == 400


def test_an_oversized_logo_is_refused(server):
    r = server["admin"].post("/api/console/branding/logo",
                             content=b"\0" * (2 * 1024 * 1024 + 10),
                             headers={"Content-Type": "image/png"})
    assert r.status_code == 400


def test_only_admins_change_the_logo(server):
    deploy = server["deploy"]
    assert deploy.post("/api/console/branding/logo", content=PNG,
                       headers={"Content-Type": "image/png"}).status_code == 403
    assert deploy.delete("/api/console/branding/logo").status_code == 403
    # אבל לראות אותו — כן.
    assert deploy.get("/api/console/branding/logo").status_code in (200, 204)


# --- SVG הוא מסמך, לא רק תמונה (#97) ----------------------------------------


def upload_svg(server, body: bytes):
    return server["admin"].post("/api/console/branding/logo", content=body,
                                headers={"Content-Type": "image/svg+xml"})


ACTIVE_SVGS = {
    "script": b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    "onload": b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><rect/></svg>',
    "handler_on_child":
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect onclick="alert(1)"/></svg>',
    "javascript_href":
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<a href="javascript:alert(1)"><rect/></a></svg>',
    "foreign_object":
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject>'
        b'<body xmlns="http://www.w3.org/1999/xhtml">hi</body></foreignObject></svg>',
    "entity":
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY x "boom">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
}


@pytest.mark.parametrize("kind", sorted(ACTIVE_SVGS))
def test_an_svg_with_active_content_is_refused(server, kind):
    """הבקרה השלילית של #97.

    לפני התיקון כל אחד מאלה נשמר כמו שהוא והוגש ב-`/branding/logo`
    כ-`image/svg+xml` על ה-origin של הקונסולה. ניווט ישיר לכתובת פותח
    אותו כ**מסמך**, ושם הסקריפט רץ.
    """
    assert upload_svg(server, ACTIVE_SVGS[kind]).status_code == 400
    assert not branding_dir(server).exists() or \
        not (branding_dir(server) / "logo.svg").is_file()


def test_a_refused_svg_does_not_replace_the_logo_that_is_there(server):
    """דחייה שמוחקת את מה שהיה גרועה מדחייה."""
    server["admin"].post("/api/console/branding/logo", content=PNG,
                         headers={"Content-Type": "image/png"})
    assert upload_svg(server, ACTIVE_SVGS["script"]).status_code == 400
    assert (branding_dir(server) / "logo.png").is_file()
    assert server["anon"].get(
        "/api/console/branding/logo").headers["content-type"] == "image/png"


@pytest.mark.parametrize("body", [
    b"\xff\xfe not utf-8 at all \x80\x81",           # לא ניתן לקרוא כטקסט
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect>',   # XML שבור
    b'<html><body>hello</body></html>',                  # לא SVG בכלל
    b"just some text",
])
def test_an_svg_we_could_not_check_is_refused(server, body):
    """עיקרון 5 על הבודק עצמו: בדיקה שנפלה חייבת להחזיר "לא", אחרת
    היא הפרצה ולא השומר עליה."""
    assert upload_svg(server, body).status_code == 400


def test_a_clean_svg_is_still_accepted(server):
    """הצד החיובי — התיקון לא הפך את SVG לסוג שאי אפשר להעלות."""
    assert upload_svg(server, SVG).status_code == 200
    assert (branding_dir(server) / "logo.svg").is_file()


def test_a_refusal_reaches_the_journal(server):
    upload_svg(server, ACTIVE_SVGS["script"])
    rows = server["admin"].get("/api/console/journal").json()
    refused = next(r for r in rows if r["event"] == "logo_refused")
    assert refused["label"] == "העלאת לוגו נדחתה"


def test_the_logo_is_served_with_headers_that_disarm_it(server):
    """השכבה השנייה: לוגו עוין שכבר יושב על הדיסק — למשל מלפני התיקון —
    לא ירוץ גם אם הבדיקה בהעלאה לא הייתה שם."""
    server["admin"].post("/api/console/branding/logo", content=PNG,
                         headers={"Content-Type": "image/png"})
    headers = server["anon"].get("/api/console/branding/logo").headers
    assert headers["x-content-type-options"] == "nosniff"
    csp = headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "sandbox" in csp
    assert headers["cache-control"] == "no-cache"      # לא נמחק בדרך
