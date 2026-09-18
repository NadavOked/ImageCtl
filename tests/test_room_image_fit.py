"""‏#953: אימג' שאינו נכנס למגירה הקטנה ביותר בחדר — מסורב בשרת, לא רק במסך.

נדב, 17/09: "שמתי כונן 256, אימג' שלא נכנס ל-256 לא אמור להופיע
באפשרויות." הרצפה היא המגירה הקטנה ביותר שדווחה ב-hello האחרון (או
בין המגירות שנבחרו, ‏#695), והסיבה נוקבת במגירה ובמספרים. בלי דיווח —
אין סירוב (בדיקה 2.7 במכונה היא הקו האחרון), והמסך מקבל `disk_floor:
null` כדי להגיד זאת. הספרייה חושפת `min_target_bytes` (הנגזר)
ו-`used_bytes` (סכום, או `null` כשמחיצה לא נמדדה).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Clock, hello_body, write_image, MANIFEST_256

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

CLONER1 = "aa:bb:cc:00:00:31"
CLONER2 = "aa:bb:cc:00:00:32"
GB = 10 ** 9
DISK_256 = 256_060_514_304          # 256.06 GB — כונן "256" אמיתי (17/09)
DISK_500 = 500_107_862_016          # 500.1 GB


def _manifest(image_id: str, name: str, windows_bytes: int) -> dict:
    """אותה גיאומטריה כמו MANIFEST_256, עם מחיצת Windows בגודל אחר —
    ‏`required_bytes` נגזר ממנה (‏#82), לא מהשדה המוצהר."""
    esp, win = MANIFEST_256["partitions"]
    return {
        **MANIFEST_256, "id": image_id, "name": name,
        "source_disk_bytes": 320 * GB, "min_target_bytes": 320 * GB,
        "partitions": [esp, {**win, "size_bytes": windows_bytes}],
    }


# סוף הפריסה = 1085440×512 + size + 1MiB → 229.56GB ו-299.56GB, כלומר
# "צריך 230GB" ו"צריך 300GB" אחרי עיגול כלפי מעלה.
IMAGE_230 = _manifest("img_fit230", "Fits 256", 229 * GB)
IMAGE_300 = _manifest("img_fit300", "Needs 300", 299 * GB)


@pytest.fixture()
def fit_server(tmp_path: Path):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415

    from server import users
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, IMAGE_230)
    write_image(images, IMAGE_300)
    app = create_app(
        tmp_path / "data", images, "http://10.44.12.10:8080",
        now_fn=Clock(), sender_runner=Recorder(), wol_send=lambda _b: None,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    for mac, name in ((CLONER1, "1"), (CLONER2, "2")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": "grp_CLONERS",
        }).status_code == 200
    yield {"admin": admin, "anon": TestClient(app)}
    ctx.sender.stop()


def hello_with_disks(client, mac: str, sizes: list[int]) -> None:
    body = hello_body(mac)
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": size, "port": i + 1,
         "model": "Drawer SSD", "serial": f"{mac[-2:]}-{i}", "removable": False,
         "scheme": "gpt", "has_data": False}
        for i, size in enumerate(sizes)
    ]
    assert client.post("/api/v1/agent/hello", json=body).status_code == 200


def open_round(client, image_id: str, **extra):
    return client.post("/api/console/room",
                       json={"image_id": image_id, "target_drives": 4, **extra})


def test_an_image_bigger_than_the_smallest_drawer_is_refused_with_the_reason(fit_server):
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])   # דיסק 2 במחשב 2

    r = open_round(admin, "img_fit300")

    assert r.status_code == 409
    assert r.json()["detail"] == "דיסק 2 במחשב 2 הוא 256GB, האימג' צריך 300GB"
    assert admin.get("/api/console/room").json()["round"] is None   # לא נפתח


def test_an_image_that_fits_the_smallest_drawer_opens(fit_server):
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])

    assert open_round(admin, "img_fit230").status_code == 200


def test_without_any_disk_report_the_server_does_not_refuse(fit_server):
    """מכונות כבויות שטרם דיווחו — אין רצפה, אין סירוב; המסך מקבל
    ‏`disk_floor: null` ומציג "גודל הדיסקים לא ידוע"."""
    admin = fit_server["admin"]
    assert admin.get("/api/console/room").json()["disk_floor"] is None
    assert open_round(admin, "img_fit300").status_code == 200


def test_a_drawer_without_a_size_is_not_a_floor(fit_server):
    """סוכן ישן בלי `size_bytes` (או 0) — "לא נמדד", לא "0 בייט"."""
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, 0])
    assert admin.get("/api/console/room").json()["disk_floor"]["size_bytes"] == DISK_500
    assert open_round(admin, "img_fit300").status_code == 200


def test_the_status_view_names_the_floor_the_screen_compares_against(fit_server):
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])

    floor = admin.get("/api/console/room").json()["disk_floor"]

    assert floor == {"mac": CLONER2, "name": "2", "port": 2, "dev": "sdb",
                     "size_bytes": DISK_256}


def test_selected_slots_that_exclude_the_small_drawer_let_the_image_through(fit_server):
    """‏#695: כשנבחרו מגירות, רק הן הרצפה — הקטנה שלא נבחרה אינה חוסמת."""
    admin, anon = fit_server["admin"], fit_server["anon"]
    hello_with_disks(anon, CLONER1, [DISK_500, DISK_500])
    hello_with_disks(anon, CLONER2, [DISK_500, DISK_256])

    refused = open_round(admin, "img_fit300",
                         target_slots=[{"mac": CLONER2, "ports": [1, 2]}])
    assert refused.status_code == 409
    assert refused.json()["detail"].startswith("דיסק 2 במחשב 2 הוא 256GB")

    assert open_round(admin, "img_fit300",
                      target_slots=[{"mac": CLONER2, "ports": [1]}]).status_code == 200


def test_the_library_list_exposes_the_derived_requirement_and_used_bytes(tmp_path):
    from server.images import ImageLibrary, required_bytes

    write_image(tmp_path, IMAGE_300)
    unmeasured = {
        **IMAGE_230, "id": "img_nomount", "name": "Hibernated",
        "partitions": [IMAGE_230["partitions"][0],
                       {**IMAGE_230["partitions"][1], "used_bytes": None}],
    }
    write_image(tmp_path, unmeasured)

    by_id = {img["id"]: img for img in ImageLibrary(tmp_path).public_list()}

    fit300 = by_id["img_fit300"]
    assert fit300["min_target_bytes"] == required_bytes(IMAGE_300)
    assert 299 * GB < fit300["min_target_bytes"] <= 300 * GB    # הנגזר, לא 320GB המוצהר
    assert fit300["used_bytes"] == 31457280 + 84509376512
    # מחיצה שלא נמדדה ("would not mount", #84) — "לא ידוע", לא 0 ולא סכום חלקי.
    assert by_id["img_nomount"]["used_bytes"] is None
