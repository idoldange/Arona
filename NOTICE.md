# License notice

Arona is licensed under the **MIT License**, see `LICENSE`. Copyright (c) 2026 idoldange.

## DAW Activity

The editor inside the Discord Activity is a modified fork of [Signal](https://github.com/ryohey/signal) by ryohey, which is
MIT licensed (Copyright (c) 2016 ryohey). The fork lives at https://github.com/idoldange/signal (branch `arona-activity`) and is
included here as the git submodule `activity/signal`. It keeps Signal's MIT license and copyright notice.

Arona talks to the editor only through the HTTP API in `arona/activity/server.py`.

[OpenUtau](https://github.com/stakira/OpenUtau) (MIT) is used as a reference for lyric editing. Code or logic ported from it
into the Signal fork keeps OpenUtau's MIT notice there.

Build-time downloads, not stored in this repository: the `A320U.sf2` and `A320U_drums.sf2` soundfonts that Signal itself
loads (`activity/build-web.sh` fetches them from the location Signal uses and serves them from this server).

## Earlier experiment

An earlier version of the Activity used [openDAW](https://github.com/andremichelle/openDAW) (AGPL-3.0-or-later). It cannot run
inside Discord Activities because it requires `SharedArrayBuffer`, so it was dropped. Arona contains no openDAW code. The
experimental changes remain in a separate public fork, https://github.com/idoldange/openDAW, under the AGPL.
A commit in this repository's history briefly changed `LICENSE` to the AGPL while that experiment was in; it was reverted.

## Third-party binaries

Engines and other third-party programs (for example Stockfish, GPL-3.0, and FFmpeg) are not redistributed from this repository.
Download them yourself and place them where the configuration expects them.
