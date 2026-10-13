# noVNC 1.6.0 (vendored)

The browser client for the VM-update screen (#1334, `server/static/vmupdate.js`).

- Source: Debian package `novnc` 1:1.6.0-2 (`/usr/share/novnc/core` and `/usr/share/novnc/vendor/pako`), unmodified.
- Upstream: https://github.com/novnc/noVNC
- License: MPL-2.0 (`LICENSE.MPL-2.0.txt`); `vendor/pako`: MIT (Expat) and Zlib; per-file terms in `COPYRIGHT.debian` (Debian's machine-readable copyright file for the package).
- Why vendored and not the Debian package on the server: the package depends on `nodejs`/`libnode115` (~57 MB installed) to ship static JavaScript.
