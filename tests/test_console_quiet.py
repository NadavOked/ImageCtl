"""‏#1195: ה-DCUI על tty1 חייב מסך שקט — הודעות קרנל נשארות ביומן.

נמדד על ESXi 22/09: אחרי ההתקנה החיה הראשונה, שורות `imagectl-drop IN=ens33 ...`
(‏IGMP שנזרק ע"י חומת האש, ברמת warning) נדפסו מעל ה-DCUI. שני צדדים: הקרנל
(‏`kernel.printk` — הקונסולה מקבלת רק crit ומעלה) וכלל ה-nft (‏`level info`)."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SYSCTL = REPO / "install" / "90-imagectl-console.conf"
SETUP = REPO / "install" / "setup-boot-server.sh"
NFT = REPO / "install" / "nftables-rules.sh"


def test_the_console_sysctl_is_shipped_and_installed():
    conf = SYSCTL.read_text(encoding="utf-8")
    values = [ln.split("=", 1) for ln in conf.splitlines() if ln.strip() and not ln.startswith("#")]
    assert [(k.strip(), v.strip()) for k, v in values] == [("kernel.printk", "3 4 1 3")]
    setup = SETUP.read_text(encoding="utf-8")
    assert 'install -m 0644 "$APP_DIR/install/90-imagectl-console.conf" /etc/sysctl.d/90-imagectl-console.conf' in setup
    # applied now, not only on the next boot -- and a failure is said, not swallowed
    assert "sysctl -q -p /etc/sysctl.d/90-imagectl-console.conf || warn" in setup


def test_the_drop_log_rule_is_info_not_warning():
    nft = NFT.read_text(encoding="utf-8")
    assert 'log prefix "imagectl-drop " level info limit rate 5/minute' in nft
    assert 'log prefix "imagectl-drop " limit rate' not in nft, "כלל log בלי level = warning = מודפס על הקונסולה"


def test_a_server_that_has_sshd_is_closed_to_loopback_at_install_time():
    """‏#729: ה-ISO בא בלי sshd (#1148); שרת שיש בו sshd האזין על כל הכרטיסים עד
    שמישהו נגע במתג. ההתקנה כותבת את אותו drop-in שהמתג מנהל, ריק מכתובות =
    לולאה מקומית בלבד (`ssh_switch.render_sshd_conf([])`), ומטעינה מחדש."""
    setup = SETUP.read_text(encoding="utf-8")
    assert 'if [[ -d /etc/ssh/sshd_config.d ]]; then' in setup
    assert "ssh_switch.render_sshd_conf([])" in setup
    assert "> /etc/ssh/sshd_config.d/imagectl-ssh.conf" in setup
    assert 'systemctl reload ssh || warn' in setup
    from server import ssh_switch
    conf = ssh_switch.render_sshd_conf([])
    assert "ListenAddress 127.0.0.1" in conf and conf.count("ListenAddress") == 1
