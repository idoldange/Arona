# License notice

Arona is licensed under the **MIT License**, see `LICENSE`. Copyright (c) 2026 idoldange.

## DAW Activity and openDAW

The DAW Activity (`activity/`) loads a modified build of [openDAW](https://github.com/andremichelle/openDAW), which is
licensed under **AGPL-3.0-or-later**. The modified openDAW is kept in its own repository (a fork) and is not part of this
repository, nor is its build output. It talks to Arona only through the HTTP API in `arona/activity/server.py`, so the two
programs stay separate works under their own licenses.

The fork carries the AGPL-3.0-or-later license and the source link that AGPL section 13 requires for network users.
Do not copy openDAW source code into this repository, and do not commit its build (`activity/web/`) here; doing so would
put AGPL code inside an MIT repository.

## Third-party binaries

Engines and other third-party programs (for example Stockfish, GPL-3.0) are not redistributed from this repository.
Download them yourself and place them where the configuration expects them.
