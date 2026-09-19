<div dir="rtl">

# `tools/iso/` — ISO ההתקנה של ImageCtl (#1139)

‏ISO אחד שמתקין דביאן 13 + ImageCtl על שרת פיזי או VM, ‏UEFI (Secure Boot
דלוק) וגם BIOS, **בלי אינטרנט** בזמן ההתקנה. בסופו השרת עולה במצב "רשת
ההפצה לא הוגדרה" — האשף (#910) / הקונסולה מקבלים את המנהל. התוכנית:
`docs/research/answers/R25-installer-iso-external.md`; ההכרעות שנקלטו:
‏R57 (Secure Boot), ‏R58 (preseed), ‏R60 (משני), ‏R61 (reproducible),
‏R62 (זיהוי הכרטיס), ‏R63 (ה-repo).

| קובץ | תפקיד |
|---|---|
| `packages.txt` | **האיחוד** של כל רשימות ה-apt: ‏`PKGS` של המתקין, הבסיס/GUI/toolbox של `build_initramfs.sh`, ומה שרק ה-ISO מוסיף. ‏`tests/test_iso_build.py` נופל אם רשימה זזה בלי הקובץ הזה |
| `make-pool.sh` | ‏`apt-get download` של הרשימה **וכל התלויות** (מול status ריק — כלום לא "כבר מותקן") → `pool/{main,contrib}/imagectl/`, ואינדקסי `dists/` (‏`--index-only` מאנדקס עץ ISO) |
| `preseed.cfg` | ‏d-i אוטומטי: mirror כבוי, חבילות מה-ISO (מסלול ה-cdrom של d-i), הדיסק הראשון שאינו USB, root בלבד, `late_command` |
| `late-command.sh` | רץ **בתוך המתקין**: מעתיק את הקוד ל-`/opt/imagectl-src`, מתקין את היחידה, ורושם ל-`/etc/imagectl/` **עובדות** — הכרטיס ש-d-i הגדיר (‏`installer-nic`), התפקיד (‏`installer-role`), המניפסט (‏`iso-release.json`) |
| `firstboot.sh` + `imagectl-firstboot.service` | האתחול הראשון: בונה `initrd.img`/`initrd.img.gui`/`vmlinuz`, ואז `setup-boot-server.sh --servers-if <הכרטיס מההתקנה>` — **בלי `--deploy-if`** |
| `build-iso.sh` | ‏netinst רשמי (מאומת sha256) + pool + קוד (`git archive` של ה-ref) + preseed + תפריט → ISO hybrid ב-`xorriso -boot_image any replay`; מדפיס `-report_el_torito` |
| `test-iso.sh` | ‏QEMU/OVMF עם Secure Boot, שני שלבים (התקנה → אתחול ראשון) — **טרם הורץ** (אין KVM במעבדה) |

## בנייה (על דביאן 13 עם אינטרנט — שרת המעבדה)

```bash
sudo tools/iso/make-pool.sh --out /root/ic-iso-tmp/repo            # פעם אחת; ~600 קבצים, ~370MB
sudo IMAGECTL_ROOT_PASSWORD='...' tools/iso/build-iso.sh \
     --out /root/ic-iso-tmp --pool /root/ic-iso-tmp/repo --ref v0.48.0
```

הפלט: `imagectl-<tag>-amd64.iso`, ‏`SHA256SUMS`, ‏`*.el_torito.txt` (הראיה
ששני קטעי האתחול — BIOS ו-UEFI — נשמרו), ‏`imagectl-iso.json`.

## מה קורה כשמאתחלים ממנו

1. תפריט (BIOS או UEFI) עם "ImageCtl server install (wipes the first
   disk)" מסומן — **ממתין ל-Enter**, אין timeout. ערך שני: שרת משני.
2. ‏d-i מתקין דביאן על הדיסק הראשון שאינו USB, בלי שאלות, בלי רשת. שם
   המחשב `imagectl-server`, ‏root עם הסיסמה שניתנה בבנייה.
3. אתחול. ‏`imagectl-firstboot` (במסך וב-journal): בונה שני initrd,
   מעתיק vmlinuz, מריץ את המתקין עם הכרטיס ש-d-i הגדיר, ומסיים ב-
   ‏`verify-boot-payload.sh`. כניסה ראשונה ב-root (במסך — **אין sshd**,
   הכרעת נדב 19/09) מחייבת החלפת סיסמה.
4. הקונסולה: `https://<כתובת>:8081`, ‏admin/admin עם החלפה כפויה. רשת
   ההפצה — מדף הרשת.

**שלושה מצבים באתחול הראשון** (`/etc/imagectl/firstboot.status`): ‏`done`;
‏`network-nic-undecidable` (d-i לא הגדיר רשת, או ה-MAC כבר לא על אף כרטיס
— **לא מנחשים**, המתקין לא רץ, ההשלמה מהאשף/ידנית עם `--servers-if`);
‏`check-error` (הבדיקה עצמה לא רצה). כישלון אדום ב-`systemctl status
imagectl-firstboot`, והיחידה רצה שוב באתחול הבא.

## מה נמדד (19/09, שרת המעבדה, QEMU 10.0 ב-TCG — אין KVM)

* ‏`xorriso -report_el_torito`: שני קטעי אתחול (BIOS `isolinux.bin`, ‏UEFI
  `efi.img`), ‏MBR/GPT/APM שוחזרו מה-netinst.
* ‏BIOS: התפריט עולה, "ImageCtl server install" מסומן, **אין טיימר**. שני
  דברים ב-netinst של דביאן 13 היו מונעים זאת ו-`build-iso.sh` מסיר אותם:
  ‏`gtk.cfg` נושא `menu default` משלו (האחרון מנצח), ו-`spkgtk.cfg` מפעיל
  **התקנה קולית אינטראקטיבית אחרי 30 שניות** בלי מגע (`timeout 300` +
  ‏`ontimeout`).
* ‏UEFI עם Secure Boot דלוק (‏`OVMF_CODE_4M.ms.fd`): ‏shim → GRUB → התפריט
  שלנו; בקרנל `Lockdown:` (= Secure Boot פעיל). ‏grub.cfg הערוך אינו שובר
  את השרשרת (R57).
* ‏`apt-cdrom add` (מה ש-d-i מריץ) על ה-ISO: 3 אינדקסים, ‏contrib נמצא,
  וכל 76 החבילות + ‏`standard` נפתרות מה-ISO **בלבד** (627 newly
  installed). ובתוך d-i אמיתי: הקרנל 6.12.107 מה-pool שלנו הותקן
  ‏`Get:1 cdrom://[...NETINST...]`.
* שתי שאלות שעצרו התקנה אמיתית גם ב-`priority=critical` ונוספו ל-preseed:
  ‏`apt-setup/cdrom/set-double` (41cdset מוצא את אותו דיסק שוב) ו-
  ‏`apt-setup/no_mirror` (netinst בלי mirror). אחריהן ‏pkgsel התקין מה-ISO.
* מלכודת שנתפסה במדידה: ‏`Release` שמונה רק `Packages.gz` — ‏apt מדלג על
  הרכיב **בשקט** ("Unable to locate package" על כל הרשימה). הפרוס נרשם גם.

## מה טרם אומת (19/09)

* **Hyper-V Gen2 + Secure Boot וברזל** — הבנייה במעבדה, ה-boot אצל נדב
  (תבנית "Microsoft UEFI Certificate Authority").
* **שרת משני**: המתקין מחייב `--primary-url`; בלי `imagectl.primary=<url>`
  בשורת האתחול firstboot נעצר בגלוי (`secondary-needs-primary`). האם
  המתקין צריך לקבל משני בלי primary (ה-pairing מהקונסולה) — הכרעת מוצר.
* ‏reproducibility — אותו ref + netinst + pool → אותו sha256: לא נמדד.
* ‏`test-iso.sh` — לא הורץ (הרצה ידנית מקבילה נעשתה ב-QEMU/TCG, ראה למעלה).
* ‏ISO שנבנה **אחרי** התיקונים של 19/09 (set-double/no_mirror, apt-repo,
  בלי openssh-server) — טרם הותקן מקצה לקצה; ההתקנה שנמדדה רצה על
  ‏ISO קודם, ושני המפתחות נענו ביד, וה-apt-repo הוזרק לדיסק ביד.

</div>
