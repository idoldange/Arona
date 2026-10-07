# Soundfonts (dung cho instrument track cua `!arona synth` .ustx)

Chon bang `sf=<ten>` (khop theo ten file, khong phan biet hoa thuong: trung ten > bat dau bang > chua) hoac tag trong ten track `[sf=<ten>]`.
Mac dinh (instrument): GeneralUser-GS -> MuseScore_General -> FluidR3_GM -> TimGM6mb -> default (chi trong cac bank GM day du).

**Cach dat ten**: file `<nhac cu>_<nguon>.sf2` (vd `piano_fluidr3.sf2`, `guitar_xxx.sf2`, `drums_909.sf2`) = soundfont chuyen cho 1 nhom nhac cu; synth.py doc ten (xem `_SF_FAMILY`) de biet font co nhung GM program nao, track ngoai pham vi tu fallback ve font mac dinh. Ten khac = bank GM day du. Cu tha them file vao thu muc la dung duoc.

| File | Nguon | License / ghi chu |
|---|---|---|
| GeneralUser-GS.sf2 | github.com/mrbumpy409/GeneralUser-GS | GeneralUser GS License (dung tu do, khong redistribute rieng le) |
| MuseScore_General.sf3 | ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General | MIT. SF3 nen -> load cham (~8s/track) |
| FluidR3_GM.sf2 | ftp.osuosl.org/pub/musescore/soundfont/fluid-soundfont.zip ("FluidR3 GM2-2") | MIT |
| FluidR3Mono_GM.sf3 | github.com/musescore/MuseScore (v2.3.2, share/sound) | MIT. Ban mono cua FluidR3 |
| TimGM6mb.sf2 | github.com/wrightflyer/SF2_SoundFonts | GPL-2, nho (6 MB) |
| orchestra_sso.sf2 | ftp.osuosl.org/pub/musescore/soundfont/Sonatina_Symphonic_Orchestra_SF2.zip | CC Sampling Plus 1.0. KHONG theo so GM: synth.py co bang doi GM->preset (strings, brass, woodwind, choir, harp, piano...); nhac cu khong co (guitar, bass, synth, drums...) tu dung soundfont mac dinh |
| drums_909.sf2 | github.com/bratpeki/soundfonts | Chi co drums (909). Track khong phai drums se fallback |
| piano / chromatic_percussion / organ / guitar / bass / ensemble / brass / reed / pipe / ethnic / percussive / drums `_fluidr3.sf2` | download.linuxaudio.org/musical-instrument-libraries/sf2/fluidr3-splitted/ | FluidR3 GM (MIT) cat theo nhom nhac cu, giu nguyen so GM. ensemble = GM 48-55 (string ensemble, choir, synth strings), reed = sax/oboe/clarinet..., pipe = flute/recorder/..., chromatic_percussion = celesta/glock/vibes/marimba... |
| ChaosBank.sf2, Jnsgm2.sf2, Masterpiece.sf2, Unison.sf2, WeedsGM3.sf2, merlin_gmv32.sf2 | github.com/bratpeki/soundfonts, github.com/wrightflyer/SF2_SoundFonts | Soundfont GM cong dong, license tuy file (thuong "free", chua kiem chung) -> chi dung ca nhan |
| default.sf2 | (co san) | dung boi utils/file_converter.py (MIDI -> wav) |
