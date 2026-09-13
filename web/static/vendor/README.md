# Vendored front-end libraries

Served from our own origin so the Content-Security-Policy can stay `script-src 'self'`
and no third-party CDN is involved at runtime.

| File | Version | Source | SHA-384 |
|---|---|---|---|
| `htmx.min.js` | 2.0.10 | npm `htmx.org@2.0.10` `dist/htmx.min.js` (jsDelivr and unpkg copies verified identical) | `sha384-H5SrcfygHmAuTDZphMHqBJLc3FhssKjG7w/CeCpFReSfwBWDTKpkzPP8c+cLsK+V` |

To upgrade: download the new version from both CDNs, confirm they match, replace the file
and update this table.
