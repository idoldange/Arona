# Soundfonts (dung cho instrument track cua `!arona synth` .ustx)

Chon bang `sf=<ten>` (khop 'chua', khong phan biet hoa thuong) hoac tag trong ten track `[sf=<ten>]`.
Mac dinh (instrument): GeneralUser-GS -> MuseScore_General -> FluidR3_GM -> TimGM6mb -> default.

| File | Nguon | License / ghi chu |
|---|---|---|
| GeneralUser-GS.sf2 | github.com/mrbumpy409/GeneralUser-GS | GeneralUser GS License (dung tu do, khong redistribute rieng le) |
| MuseScore_General.sf3 | ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General | MIT. SF3 nen -> load cham (~8s/track) |
| FluidR3_GM.sf2 | ftp.osuosl.org/pub/musescore/soundfont/fluid-soundfont.zip ("FluidR3 GM2-2") | MIT |
| FluidR3Mono_GM.sf3 | github.com/musescore/MuseScore (v2.3.2, share/sound) | MIT. Ban mono cua FluidR3 |
| TimGM6mb.sf2 | github.com/wrightflyer/SF2_SoundFonts | GPL-2, nho (6 MB) |
| SSO_Sonatina_Symphonic_Orchestra.sf2 | ftp.osuosl.org/pub/musescore/soundfont/Sonatina_Symphonic_Orchestra_SF2.zip | CC Sampling Plus 1.0. KHONG theo so GM: synth.py co bang doi GM->preset (strings, brass, woodwind, choir, harp, piano...); nhac cu khong co (guitar, bass, synth, drums...) tu dung soundfont mac dinh |
| 909_drum_sf.sf2 | github.com/bratpeki/soundfonts | Chi co drums (909). Track khong phai drums se fallback |
| ChaosBank.sf2, Jnsgm2.sf2, Masterpiece.sf2, Unison.sf2, WeedsGM3.sf2, merlin_gmv32.sf2 | github.com/bratpeki/soundfonts, github.com/wrightflyer/SF2_SoundFonts | Soundfont GM cong dong, license tuy file (thuong "free", chua kiem chung) -> chi dung ca nhan |
| default.sf2 | (co san) | dung boi utils/file_converter.py (MIDI -> wav) |
