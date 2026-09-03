"""הגשת קבצי האתחול של הסוכן — הפער שמעבדת ה-VM חשפה (issue #12).

המתקין מניח vmlinuz ו-initrd.img ב-HTTP_ROOT והתפריט מפנה אליהם;
השרת חייב (א) להגיש אותם תחת ‎/boot, ו-(ב) לסגור את החיבור אחרי כל
תשובת ‎/boot — GRUB לא מזהה content-length באותיות קטנות של uvicorn,
וקריאה-עד-EOF על חיבור פתוח נתקעת עד timeout.
"""

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.app import create_app


def make_client(tmp_path, with_boot=True):
    boot = tmp_path / "boot"
    if with_boot:
        boot.mkdir()
        (boot / "vmlinuz").write_bytes(b"fake-kernel")
        (boot / "initrd.img").write_bytes(b"fake-initramfs")
    app = create_app(tmp_path / "data", tmp_path / "images",
                     "http://127.0.0.1:8080", boot_dir=boot)
    return TestClient(app)


def test_boot_files_are_served(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/boot/vmlinuz")
    assert response.status_code == 200
    assert response.content == b"fake-kernel"
    assert response.headers["connection"] == "close"


def test_boot_menu_still_wins_over_the_mount(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/boot/menu?mac=aa:bb:cc:dd:ee:ff")
    assert response.status_code == 200
    assert b"chain_local" in response.content
    assert response.headers["connection"] == "close"


def test_missing_boot_dir_is_not_fatal(tmp_path):
    client = make_client(tmp_path, with_boot=False)
    response = client.get("/boot/menu?mac=aa:bb:cc:dd:ee:ff")
    assert response.status_code == 200
    assert client.get("/boot/vmlinuz").status_code == 404
