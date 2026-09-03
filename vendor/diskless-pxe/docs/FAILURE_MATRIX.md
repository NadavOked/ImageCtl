# Failure matrix

| Failure | Required behavior before integration approval |
|---|---|
| DHCP unavailable | firmware/local boot policy; never disk write |
| boot HTTP unavailable | local boot/fail closed |
| kernel/initramfs missing | local boot/fail closed |
| unknown MAC | local boot |
| unknown interface version | local boot |
| kiosk URL unavailable | retry with bounded backoff; diagnostics available; no disk write |
| no imaging task | no disk write |
| target disk ambiguous | refuse operation |
| multicast interrupted | task fails visibly; never report success without positive evidence |
| server disappears mid-task | fail task and preserve diagnostics |
