"""Panel (embed + select/button/modal) cho `!arona synth` khi lenh duoc gui KHONG kem file hop le.

Thay vi in huong dan dai, bot gui 1 embed:
  - nut Upload mo modal chon file (.ust/.ustx/.mid/.midi + anh/video/audio tuy chon) -> embed tu cap nhat,
  - chon track -> chon nhac cu (Auto = doan theo ten track nhu cu, GM co san, Drums, Vocal, Mute, hoac Custom: modal chon soundfont + tim ten -> dropdown ket qua),
  - chinh am luong tung track (%), config chung (transpose, lang, temperature, top_k, inst_vol),
  - bam Run -> goi run_cb (main._run_synth_job) y het luc gui file kem lenh.
Chi nguoi go lenh moi dung duoc panel. Panel het han sau SESSION_TTL_S giay.
"""
import asyncio
import dataclasses
import os
import re
import shutil
import tempfile

import discord

from console import console
import arona.tts.synth as S
from arona.tts.synth_media import media_kind, MEDIA_MAX_MB

SESSION_TTL_S = 1800
SESSIONS: dict = {}   # (channel_id, user_id) -> SynthSession

PROJ_EXTS = (".ust", ".ustx", ".mid", ".midi")
PROJ_MAX_MB = {".ust": 1, ".ustx": 6, ".mid": 2, ".midi": 2}
MB = 1024 * 1024
SF_EXTS = (".sf2", ".sf3")
SF_MAX_MB = 200      # per soundfont
SF_TOTAL_MB = 400    # per panel
COLOR = 0x4FC3F7

# (ten hien thi, GM program) - toi da 21 muc de cong voi Auto/Vocal/Mute/Drums van <= 25 muc cua Select
GM_CHOICES = [
    ("Piano", 0), ("Electric piano", 4), ("Organ", 16), ("Nylon guitar", 24), ("Steel guitar", 25),
    ("Clean e-guitar", 27), ("Distortion guitar", 30), ("Acoustic bass", 32), ("Electric bass", 33),
    ("Synth bass", 38), ("Violin", 40), ("Cello", 42), ("Strings", 48), ("Harp", 46), ("Choir", 52),
    ("Trumpet", 56), ("Trombone", 57), ("French horn", 60), ("Brass", 61), ("Sax", 65), ("Flute", 73),
]


def _fmt_size(n: int) -> str:
    return f"{n / MB:.1f} MB" if n >= MB else f"{max(1, n // 1024)} KB"


@dataclasses.dataclass
class TrackInfo:
    fname: str
    kind: str            # "ust" | "ustx" | "midi"
    track: "S.UTrack"    # da classify_track, notes de rong (chi giu so luong o `notes`)
    notes: int
    over: dict = dataclasses.field(default_factory=dict)


def _parse_file(name: str, data: bytes) -> list:
    fmt = S.detect_format(data)
    if fmt == "ust":
        return [TrackInfo(name, "ust", S.UTrack(idx=0, name=name, role="vocal"), 0)]
    proj = S.parse_midi(data) if fmt == "midi" else S.parse_ustx(data)
    out = []
    for t in proj.tracks:
        S.classify_track(t)
        if not t.notes:
            continue
        out.append(TrackInfo(name, fmt, dataclasses.replace(t, notes=[]), len(t.notes)))
    return out


def _label(ti: TrackInfo) -> str:
    """Mo ta ngan gon track sau khi ap lua chon cua nguoi dung: nhac cu that su se dung + am luong."""
    vol = ti.over.get("vol")
    vol_txt = "" if vol is None else f" · {vol:g}%"
    if ti.kind == "ust":
        return "🎤 vocal (UTAU)" + vol_txt
    t = dataclasses.replace(ti.track)
    S.apply_track_override(t, ti.over)
    if t.mute:
        return "🔇 muted"
    if t.role == "vocal":
        return "🎤 vocal" + vol_txt
    pk = S.pick_soundfont(t, t.sf_hint, t.preset)
    if not pk:
        return "🎹 (no soundfont)" + vol_txt
    name = pk.name or ("drum kit" if t.drums else f"GM {t.program}")
    sf = os.path.splitext(os.path.basename(pk.sf))[0]
    auto = "" if ti.over.get("role") else " (auto)"
    return f"{'🥁' if t.drums else '🎹'} {name} · {sf}{auto}{vol_txt}"


class SynthSession:
    def __init__(self, message, opts: dict, media_opts: dict, run_cb):
        self.message = message
        self.key = (message.channel.id, message.author.id)
        self.author_id = message.author.id
        self.opts = opts
        self.media_opts = media_opts
        self.run_cb = run_cb
        self.files: list = []     # [(name, bytes)]  .ust/.ustx/.mid
        self.media: list = []     # [(name, kind, bytes)]
        self.infos: list = []     # [TrackInfo]
        self.fonts: list = []     # [(stem, path)] soundfont nguoi dung upload (thu muc tam, xoa khi panel dong / job xong)
        self.font_dir = None
        self.labels: list = []
        self.sel = 0
        self.notes: list = []
        self.msg = None
        self.view = None
        self.started = False
        self.uploading = False
        self.lock = asyncio.Lock()

    # ---- state -------------------------------------------------------------------------------------------------
    def current(self):
        return self.infos[self.sel] if 0 <= self.sel < len(self.infos) else None

    def _unique(self, fn: str) -> str:
        used = {n for n, _ in self.files} | {n for n, _, _ in self.media}
        if fn not in used:
            return fn
        base, ext = os.path.splitext(fn)
        i = 2
        while f"{base} ({i}){ext}" in used:
            i += 1
        return f"{base} ({i}){ext}"

    def _note(self, txt: str):
        self.notes.append(txt)
        self.notes = self.notes[-4:]

    def font_paths(self) -> list:
        return [p for _, p in self.fonts]

    def cleanup(self):
        if self.font_dir:
            shutil.rmtree(self.font_dir, ignore_errors=True)
        self.font_dir = None
        self.fonts = []

    async def _add_font(self, att) -> bool:
        fn = att.filename
        if att.size > SF_MAX_MB * MB:
            self._note(f"`{fn}` is too large (max {SF_MAX_MB} MB per soundfont)")
            return False
        used = sum(os.path.getsize(p) for p in self.font_paths() if os.path.isfile(p))
        if used + att.size > SF_TOTAL_MB * MB:
            self._note(f"soundfont limit reached ({SF_TOTAL_MB} MB total) — `{fn}` skipped")
            return False
        if len(self.fonts) >= 10:
            self._note("max 10 soundfonts per panel")
            return False
        try:
            data = await att.read()
        except Exception as e:
            self._note(f"couldn't read `{fn}`: {e}")
            return False
        if self.font_dir is None:
            self.font_dir = tempfile.mkdtemp(prefix="synth_fonts_")
        base, ext = os.path.splitext(fn)
        safe = re.sub(r"[^\w\-]+", "_", base)[:60].strip("_") or "font"
        stem, i = f"user_{safe}", 2
        while any(s == stem for s, _ in self.fonts):
            stem, i = f"user_{safe}_{i}", i + 1
        path = os.path.join(self.font_dir, stem + ext.lower())

        def _write_and_check():
            with open(path, "wb") as f:
                f.write(data)
            return S._presets(path)

        try:
            presets = await asyncio.to_thread(_write_and_check)
        except Exception:
            presets = {}
        if not presets:
            try:
                os.remove(path)
            except OSError:
                pass
            self._note(f"`{fn}` isn't a valid soundfont")
            return False
        self.fonts.append((stem, path))
        return True

    async def relabel(self):
        S.set_user_fonts(self.font_paths())
        infos = list(self.infos)
        self.labels = await asyncio.to_thread(lambda: [_label(ti) for ti in infos])
        if self.sel >= len(self.infos):
            self.sel = 0

    async def add_attachments(self, atts, refresh: bool = True):
        added = False
        async with self.lock:
            self.notes = []
            for att in atts:
                fn = att.filename
                low = fn.lower()
                if low.endswith(SF_EXTS):
                    await self._add_font(att)
                    continue
                ext = next((e for e in PROJ_EXTS if low.endswith(e)), None)
                mk = None if ext else media_kind(fn)
                if ext:
                    if att.size > PROJ_MAX_MB[ext] * MB:
                        self._note(f"`{fn}` is too large (max {PROJ_MAX_MB[ext]} MB for {ext})")
                        continue
                elif mk:
                    if att.size > MEDIA_MAX_MB[mk] * MB:
                        self._note(f"`{fn}` is too large (max {MEDIA_MAX_MB[mk]} MB for {mk})")
                        continue
                else:
                    self._note(f"`{fn}`: unsupported file type")
                    continue
                try:
                    data = await att.read()
                except Exception as e:
                    self._note(f"couldn't read `{fn}`: {e}")
                    continue
                name = self._unique(fn)
                if ext:
                    try:
                        infos = await asyncio.to_thread(_parse_file, name, data)
                    except Exception as e:
                        self._note(f"`{fn}` can't be parsed: {e}")
                        continue
                    if not infos:
                        self._note(f"`{fn}` has no tracks with notes")
                        continue
                    self.files.append((name, data))
                    self.infos.extend(infos)
                else:
                    self.media.append((name, mk, data))
                added = True
            if added or self.notes:
                await self.relabel()
        if refresh:
            await self.refresh_message()

    # ---- render ------------------------------------------------------------------------------------------------
    def config_text(self) -> str:
        o = self.opts
        return (
            f"transpose **{o.get('transpose', 'auto')}** · lang **{o.get('text_lang', 'ja')}** · "
            f"temperature **{o.get('temperature', S.DEFAULT_SYNTH_TEMPERATURE)}** · top_k **{o.get('top_k', 'default')}** · "
            f"instrument vol **{o.get('inst_vol', f'{S.DEFAULT_INST_PCT:g}')}%**"
        )

    def embed(self, status: str | None = None) -> discord.Embed:
        e = discord.Embed(title="🎹 Arona Synth", color=COLOR)
        if status:
            e.description = status
            return e
        desc = []
        if not self.files and not self.media:
            desc.append(
                "**1. Upload** — press 📎 **Upload files** and pick your file(s):\n"
                "`.ustx` (OpenUtau) · `.ust` (UTAU) · `.mid` / `.midi` — optionally images / videos / audio to make an mp4, "
                "or your own `.sf2` / `.sf3` soundfont (use it via 🎻 **Custom instrument**; deleted after the job).\n"
                "**2. Tweak** — pick a track, choose its instrument and volume (Auto keeps my guess).\n"
                "**3. Run** — press ▶ **Run** when you're happy."
            )
        else:
            desc.append("Pick a track below, change its instrument / volume, then press ▶ **Run**. Press 📎 **Upload files** to add more.")
        if self.infos:
            lines = []
            for i, ti in enumerate(self.infos[:25]):
                mark = "▶" if i == self.sel else "▫️"
                lines.append(f"{mark} `{i + 1}.` **{ti.track.name[:40]}** — {self.labels[i] if i < len(self.labels) else '…'}")
            if len(self.infos) > 25:
                lines.append(f"-# +{len(self.infos) - 25} more tracks (kept on Auto)")
            desc.append("\n".join(lines))
        e.description = "\n\n".join(desc)[:4000]
        if self.files or self.media or self.fonts:
            fl = [f"`{n}` ({_fmt_size(len(d))})" for n, d in self.files] + [f"`{n}` ({k})" for n, k, _ in self.media]
            fl += [f"🎼 `{s}` ({_fmt_size(os.path.getsize(p))})" for s, p in self.fonts if os.path.isfile(p)]
            txt = ", ".join(fl)
            e.add_field(name="📁 Files", value=(txt if len(txt) <= 1000 else txt[:997] + "…"), inline=False)
        e.add_field(name="⚙️ Config", value=self.config_text(), inline=False)
        if self.notes:
            e.add_field(name="⚠️", value="\n".join(self.notes)[:1000], inline=False)
        e.set_footer(text="Only the person who ran the command can use this panel · expires after 30 min idle")
        return e

    async def refresh_message(self):
        if not self.msg or self.started:
            return
        self.view.sync()
        try:
            await self.msg.edit(embed=self.embed(), view=self.view)
        except Exception as e:
            console.log(f"Synth panel refresh failed: {e}", "WARN")

    # ---- lifecycle ---------------------------------------------------------------------------------------------
    async def open(self):
        self.view = SynthView(self)
        self.view.sync()
        self.msg = await self.message.channel.send(
            embed=self.embed(), view=self.view,
            reference=self.message.to_reference(fail_if_not_exists=False), mention_author=False,
        )

    async def close(self, status: str):
        self.started = True
        self.cleanup()
        SESSIONS.pop(self.key, None)
        if self.view:
            self.view.stop()
        if self.msg:
            try:
                await self.msg.edit(embed=self.embed(status), view=None)
            except Exception:
                pass

    def has_input(self) -> bool:
        return bool(self.files) or any(k in ("audio", "video") for _, k, _ in self.media)

    def build_job(self):
        synth_inputs = [(n, d, "application/octet-stream") for n, d in self.files]
        media_inputs = [(n, d) for n, _, d in self.media]
        by_file: dict = {}
        for ti in self.infos:
            if ti.over:
                by_file.setdefault(ti.fname, {})[ti.track.idx] = dict(ti.over)
        opts = dict(self.opts)
        if self.fonts:
            opts["user_fonts"] = self.font_paths()
        if len(synth_inputs) == 1:
            opts["track_over"] = by_file.get(synth_inputs[0][0], {})
        elif by_file:
            opts["track_over_by_file"] = by_file
        names = [n for n, _ in self.files] + [n for n, _, _ in self.media]
        return synth_inputs, media_inputs, opts, dict(self.media_opts), names

    async def run(self, interaction: discord.Interaction):
        synth_inputs, media_inputs, opts, media_opts, names = self.build_job()
        self.started = True
        SESSIONS.pop(self.key, None)
        self.view.stop()
        await interaction.response.edit_message(embed=self.embed("▶ Started — progress is in the next message."), view=None)
        try:
            await self.run_cb(self.message, synth_inputs, media_inputs, opts, media_opts, names)
        except Exception as e:
            console.log(f"Synth panel job failed: {e}", "ERROR")
            try:
                await self.message.channel.send(f"-# Synth error: {e}")
            except Exception:
                pass
        finally:
            self.cleanup()
            try:
                await self.msg.delete()
            except Exception:
                pass


# ===================================================================================================================
#  View / modals
# ===================================================================================================================
class SynthView(discord.ui.View):
    def __init__(self, s: SynthSession):
        super().__init__(timeout=SESSION_TTL_S)
        self.s = s
        self.track_sel = discord.ui.Select(placeholder="Track (upload a file first)", options=[discord.SelectOption(label="(no tracks yet)", value="-")], row=0, disabled=True)
        self.track_sel.callback = self._on_track
        self.inst_sel = discord.ui.Select(placeholder="Instrument", options=[discord.SelectOption(label="(no track selected)", value="-")], row=1, disabled=True)
        self.inst_sel.callback = self._on_inst
        self.btn_upload = discord.ui.Button(label="Upload files", emoji="📎", style=discord.ButtonStyle.primary, row=2)
        self.btn_upload.callback = self._on_upload
        self.btn_vol = discord.ui.Button(label="Volume", emoji="🔊", style=discord.ButtonStyle.secondary, row=2)
        self.btn_vol.callback = self._on_volume
        self.btn_custom = discord.ui.Button(label="Custom instrument", emoji="🎻", style=discord.ButtonStyle.secondary, row=2)
        self.btn_custom.callback = self._on_custom
        self.btn_cfg = discord.ui.Button(label="Config", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
        self.btn_cfg.callback = self._on_config
        self.btn_run = discord.ui.Button(label="Run", emoji="▶️", style=discord.ButtonStyle.success, row=3)
        self.btn_run.callback = self._on_run
        self.btn_daw = discord.ui.Button(label="Open DAW", emoji="🎹", style=discord.ButtonStyle.secondary, row=3)
        self.btn_daw.callback = self._on_daw
        self.btn_cancel = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, row=3)
        self.btn_cancel.callback = self._on_cancel
        for item in (self.track_sel, self.inst_sel, self.btn_upload, self.btn_vol, self.btn_custom, self.btn_cfg, self.btn_run, self.btn_daw, self.btn_cancel):
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.s.author_id:
            await interaction.response.send_message("This panel belongs to someone else — run `!arona synth` to get your own.", ephemeral=True)
            return False
        return True

    async def _on_daw(self, interaction: discord.Interaction):
        """Cach DUY NHAT de mo Activity: tao session cho kenh voice cua nguoi bam, roi launch. Khong co session -> API tra 403."""
        voice = getattr(interaction.user, "voice", None)
        if not (voice and voice.channel):
            await interaction.response.send_message("Join a voice channel first, then press **Open DAW** again.", ephemeral=True)
            return
        from arona.activity.sessions import registry
        from arona.activity.imports import build_imports
        imports = build_imports(list(self.s.files))     # ustx/mid dang co trong panel -> Activity tai ve khi mo
        registry.create(voice.channel.id, interaction.guild_id, interaction.user.id,
                        text_channel_id=interaction.channel_id, imports=imports)
        try:
            await interaction.response.launch_activity()
        except Exception as e:
            console.log(f"Open DAW failed: {e}", "ERROR")
            registry.end(voice.channel.id)
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Couldn't launch the DAW: `{e}`", ephemeral=True)

    async def on_timeout(self):
        if not self.s.started:
            await self.s.close("⏱️ Synth panel expired. Run `!arona synth` again.")

    def sync(self):
        s = self.s
        if s.infos:
            opts = []
            for i, ti in enumerate(s.infos[:25]):
                opts.append(discord.SelectOption(
                    label=(f"{i + 1}. {ti.track.name}" or f"Track {i + 1}")[:100],
                    description=(s.labels[i] if i < len(s.labels) else "")[:100] or None,
                    value=str(i), default=(i == s.sel),
                ))
            self.track_sel.options = opts
            self.track_sel.placeholder = "Track"
            self.track_sel.disabled = False
        else:
            self.track_sel.options = [discord.SelectOption(label="(no tracks yet)", value="-")]
            self.track_sel.placeholder = "Track (upload a file first)"
            self.track_sel.disabled = True
        cur = s.current()
        if cur is not None and cur.kind != "ust":
            opts = [discord.SelectOption(label="Auto (guess from track name)", value="auto", emoji="✨")]
            if cur.kind == "ustx":
                opts.append(discord.SelectOption(label="Vocal (Arona sings)", value="vocal", emoji="🎤"))
            opts.append(discord.SelectOption(label="Drums", value="drums", emoji="🥁"))
            opts += [discord.SelectOption(label=n, value=f"gm:{p}") for n, p in GM_CHOICES]
            opts.append(discord.SelectOption(label="Mute track", value="mute", emoji="🔇"))
            self.inst_sel.options = opts[:25]
            self.inst_sel.placeholder = f"Instrument for: {cur.track.name}"[:150]
            self.inst_sel.disabled = False
        else:
            self.inst_sel.options = [discord.SelectOption(label="(no track selected)", value="-")]
            self.inst_sel.placeholder = "UTAU .ust is always sung — use Volume" if cur is not None else "Instrument"
            self.inst_sel.disabled = True
        self.btn_upload.label = "Uploading..." if s.uploading else "Upload files"
        self.btn_upload.emoji = "⏳" if s.uploading else "📎"
        self.btn_upload.disabled = s.uploading
        self.btn_vol.disabled = cur is None
        self.btn_custom.disabled = cur is None or cur.kind == "ust"
        self.btn_run.disabled = not s.has_input()

    async def after(self, interaction: discord.Interaction):
        await self.s.relabel()
        self.sync()
        await interaction.response.edit_message(embed=self.s.embed(), view=self)

    # ---- callbacks ---------------------------------------------------------------------------------------------
    async def _on_track(self, interaction: discord.Interaction):
        try:
            self.s.sel = int(self.track_sel.values[0])
        except (ValueError, IndexError):
            pass
        await self.after(interaction)

    async def _on_inst(self, interaction: discord.Interaction):
        ti = self.s.current()
        if ti is None:
            await interaction.response.defer()
            return
        v = self.inst_sel.values[0]
        for k in ("role", "drums", "program", "preset", "sf"):
            ti.over.pop(k, None)
        if v == "vocal":
            ti.over["role"] = "vocal"
        elif v == "mute":
            ti.over["role"] = "mute"
        elif v == "drums":
            ti.over.update(role="inst", drums=True, program=0, preset=None, sf=None)
        elif v.startswith("gm:"):
            ti.over.update(role="inst", drums=False, program=int(v[3:]), preset=None, sf=None)
        await self.after(interaction)

    async def _on_upload(self, interaction: discord.Interaction):
        await interaction.response.send_modal(UploadModal(self.s))

    async def _on_volume(self, interaction: discord.Interaction):
        ti = self.s.current()
        if ti is not None:
            await interaction.response.send_modal(VolumeModal(self.s, ti))

    async def _on_custom(self, interaction: discord.Interaction):
        ti = self.s.current()
        if ti is not None:
            await interaction.response.send_modal(CustomInstModal(self.s, ti))

    async def _on_config(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ConfigModal(self.s))

    async def _on_run(self, interaction: discord.Interaction):
        if not self.s.has_input():
            await interaction.response.send_message("Upload at least one `.ustx` / `.ust` / `.mid` file (or an audio / video file) first.", ephemeral=True)
            return
        await self.s.run(interaction)

    async def _on_cancel(self, interaction: discord.Interaction):
        self.s.started = True
        self.s.cleanup()
        SESSIONS.pop(self.s.key, None)
        self.stop()
        await interaction.response.edit_message(embed=self.s.embed("Cancelled."), view=None)


class VolumeModal(discord.ui.Modal):
    def __init__(self, s: SynthSession, ti: TrackInfo):
        super().__init__(title=f"Volume: {ti.track.name}"[:45])
        self.s, self.ti = s, ti
        cur = ti.over.get("vol")
        self.vol = discord.ui.TextInput(label="Volume % (100 = unchanged, 0 = mute)", default=f"{cur:g}" if cur is not None else "100", max_length=6, required=True)
        self.add_item(self.vol)

    async def on_submit(self, interaction: discord.Interaction):
        raw = self.vol.value.strip().rstrip("%").strip()
        try:
            pct = max(0.0, min(400.0, float(raw)))
        except ValueError:
            await interaction.response.send_message(f"`{self.vol.value}` isn't a number (try 100, 60, 150…).", ephemeral=True)
            return
        if abs(pct - 100.0) < 0.01:
            self.ti.over.pop("vol", None)
        else:
            self.ti.over["vol"] = pct
        await self.s.view.after(interaction)


class UploadModal(discord.ui.Modal):
    def __init__(self, s: SynthSession):
        super().__init__(title="Upload files")
        self.s = s
        self.files = discord.ui.FileUpload(min_values=1, max_values=10, required=True)
        self.add_item(discord.ui.Label(text="Files", description=".ustx · .ust · .mid · .sf2/.sf3 soundfont · images · videos · audio", component=self.files))

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        atts = list(self.files.values)
        console.log(f"Synth panel upload: {[(a.filename, a.size) for a in atts]}", "INFO")
        self.s.uploading = True
        try:
            self.s.view.sync()
            await self.s.msg.edit(view=self.s.view)
        except Exception as e:
            console.log(f"Synth panel uploading-state edit failed: {e}", "WARN")
        try:
            await self.s.add_attachments(atts, refresh=False)
        except Exception as e:
            import traceback
            console.log(f"Synth panel upload failed: {e}\n{traceback.format_exc()}", "ERROR")
            try:
                await interaction.followup.send(f"Upload failed: `{e}`", ephemeral=True)
            except Exception:
                pass
        finally:
            self.s.uploading = False
            await self.s.refresh_message()

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        import traceback
        console.log(f"Synth upload modal error: {error}\n{''.join(traceback.format_exception(error))}", "ERROR")


def _apply_preset(ti: TrackInfo, bank: int, prog: int, path: str, name: str):
    ti.over.update(role="inst", drums=(bank == 128), program=(0 if bank == 128 else prog), preset=name,
                   sf=os.path.splitext(os.path.basename(path))[0])


def _browse(font: str) -> list:
    """Danh sach preset cua 1 soundfont (khi chi chon font, khong go ten) -> cung dang tuple voi S.search_presets."""
    paths = S._match_soundfonts(font)
    if not paths:
        return []
    p = paths[0]
    items = [(0, 0, len(n), b, pr, p, n) for (b, pr), n in S._presets(p).items()]
    items.sort(key=lambda r: (r[3] == 128, r[6].lower()))
    return items


class PickView(discord.ui.View):
    """Dropdown ket qua tim nhac cu (ephemeral)."""
    def __init__(self, s: SynthSession, ti: TrackInfo, res: list):
        super().__init__(timeout=300)
        self.s, self.ti, self.res = s, ti, res
        opts = []
        for i, (_, _, _, bank, prog, path, name) in enumerate(res):
            sf = os.path.splitext(os.path.basename(path))[0]
            opts.append(discord.SelectOption(
                label=(("🥁 " if bank == 128 else "") + name)[:100] or "?",
                description=f"{sf} · {'drum kit' if bank == 128 else f'bank {bank}'} · #{prog}"[:100], value=str(i)))
        self.sel = discord.ui.Select(placeholder="Choose an instrument", options=opts)
        self.sel.callback = self._on_pick
        self.add_item(self.sel)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.s.author_id

    async def _on_pick(self, interaction: discord.Interaction):
        _, _, _, bank, prog, path, name = self.res[int(self.sel.values[0])]
        _apply_preset(self.ti, bank, prog, path, name)
        self.stop()
        await interaction.response.edit_message(content=f"✅ **{self.ti.track.name}** → {name}", view=None)
        await self.s.relabel()
        await self.s.refresh_message()


class CustomInstModal(discord.ui.Modal):
    def __init__(self, s: SynthSession, ti: TrackInfo):
        super().__init__(title="Custom instrument")
        self.s, self.ti = s, ti
        mine = [stem for stem, _ in s.fonts][:24]
        fonts = mine + S.list_soundfonts()[: max(0, 24 - len(mine))]
        self.font = discord.ui.Select(
            placeholder="Any soundfont (best match)", required=False,
            options=[discord.SelectOption(label="Any soundfont (best match)", value="*")]
            + [discord.SelectOption(label=(("📎 " + f) if f in mine else f)[:100], value=f[:100]) for f in fonts],
        )
        self.query = discord.ui.TextInput(placeholder="violin · nylon guitar · 25 (GM number) · blank = browse the soundfont", required=False, max_length=80)
        self.add_item(discord.ui.Label(text="Soundfont", component=self.font))
        self.add_item(discord.ui.Label(text="Instrument", description="Search by name, or type a GM number", component=self.query))

    async def on_submit(self, interaction: discord.Interaction):
        S.set_user_fonts(self.s.font_paths())
        font = next((v for v in self.font.values if v != "*"), None)
        q = self.query.value.strip()
        if q.isdigit() and 0 <= int(q) <= 127:
            self.ti.over.update(role="inst", drums=False, program=int(q), preset=None, sf=None)
            await self.s.view.after(interaction)
            return
        if ":" in q and not font:
            font, q = S.split_sf_spec(q)
            q = q or ""
        if q:
            res = await asyncio.to_thread(S.search_presets, q, font, None)
        elif font:
            res = await asyncio.to_thread(_browse, font)
        else:
            await interaction.response.send_message("Pick a soundfont and/or type an instrument name.", ephemeral=True)
            return
        if not res:
            if q:
                hint = await asyncio.to_thread(S.preset_search_text, f"{font}:{q}" if font else q, 900)
            else:
                hint = "No instruments found in that soundfont."
            await interaction.response.send_message(f"Couldn't find an instrument.\n{hint}", ephemeral=True)
            return
        if len(res) == 1:
            _, _, _, bank, prog, path, name = res[0]
            _apply_preset(self.ti, bank, prog, path, name)
            await self.s.view.after(interaction)
            return
        extra = f" (first 25 of {len(res)} — type more to narrow)" if len(res) > 25 else ""
        await interaction.response.send_message(
            f"**{len(res)} matches** for **{self.ti.track.name}**{extra}", view=PickView(self.s, self.ti, res[:25]), ephemeral=True)


class ConfigModal(discord.ui.Modal):
    def __init__(self, s: SynthSession):
        super().__init__(title="Synth config (blank = default)")
        self.s = s
        o = s.opts
        self.f_tr = discord.ui.TextInput(label="Transpose (semitones, blank = auto octave)", default=str(o.get("transpose", "")), required=False, max_length=4)
        self.f_lang = discord.ui.TextInput(label="Lyrics language (ja / en / zh / ko / yue)", default=str(o.get("text_lang", "")), required=False, max_length=8)
        self.f_temp = discord.ui.TextInput(label=f"Temperature (default {S.DEFAULT_SYNTH_TEMPERATURE})", default=str(o.get("temperature", "")), required=False, max_length=5)
        self.f_topk = discord.ui.TextInput(label="top_k (blank = server default)", default=str(o.get("top_k", "")), required=False, max_length=4)
        self.f_ivol = discord.ui.TextInput(label=f"Instrument volume % vs vocal (default {S.DEFAULT_INST_PCT:g})", default=str(o.get("inst_vol", "")), required=False, max_length=6)
        for f in (self.f_tr, self.f_lang, self.f_temp, self.f_topk, self.f_ivol):
            self.add_item(f)

    async def on_submit(self, interaction: discord.Interaction):
        o, errs = self.s.opts, []

        def put(key, raw, cast, label):
            raw = (raw or "").strip()
            if not raw or raw.lower() in ("auto", "default"):
                o.pop(key, None)
                return
            try:
                cast(raw.rstrip("%") if key == "inst_vol" else raw)
            except ValueError:
                errs.append(f"{label}: `{raw}` isn't valid")
                return
            o[key] = raw.rstrip("%") if key == "inst_vol" else raw

        put("transpose", self.f_tr.value, int, "Transpose")
        put("temperature", self.f_temp.value, float, "Temperature")
        put("top_k", self.f_topk.value, int, "top_k")
        put("inst_vol", self.f_ivol.value, float, "Instrument volume")
        lang = self.f_lang.value.strip().lower()
        if lang:
            o["text_lang"] = {"jp": "ja", "japanese": "ja", "english": "en", "eng": "en"}.get(lang, lang)
        else:
            o.pop("text_lang", None)
        self.s.notes = errs
        await self.s.view.after(interaction)


# ===================================================================================================================
#  Entry points (main.py)
# ===================================================================================================================
async def open_setup(message, opts: dict, media_opts: dict, attachments, run_cb):
    """`!arona synth` khong kem file hop le -> mo panel. Attachment san co (vd chi co anh) duoc nap luon."""
    key = (message.channel.id, message.author.id)
    old = SESSIONS.pop(key, None)
    if old:
        await old.close("Replaced by a newer synth panel.")
    s = SynthSession(message, dict(opts), dict(media_opts), run_cb)
    SESSIONS[key] = s
    await s.add_attachments(list(attachments or []), refresh=False)
    await s.open()
