# Huong dan hieu va tao module moi

Tai lieu nay mo ta convention dang duoc dung trong Arona va quy trinh them feature moi. Day la huong dan theo source hien tai, khong phai cam ket ve mot plugin system: phan lon feature duoc noi thu cong vao `main.py`.

Tai lieu kien truc tong the: [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md). Entry point Discord: [main.py](main.py). Runtime config: [config.py](config.py).

## 1. Mo hinh module hien tai

Python module trong repo thuong la mot file `.py` co public function/class de module khac import. Chua co co che tu quet thu muc, auto-register hoac nap plugin. Import mot module khong tu dong lam no thanh Discord command hay Gemini tool.

| Khu vuc | Dung cho | Vi du |
|---|---|---|
| `utils/` | Service, storage, API integration, Discord UI va helper dung chung | `todo.py`, `memory.py`, `github.py`, `discord_ui.py` |
| `arona/` | Persona, voice, TTS va activity cua Arona | `prompt.py`, `tts/`, `voice_engine/` |
| `affection/` | Mood, bond va facade quan ly tinh cam | `manager.py`, `mood.py`, `bond.py` |
| `games/` | Logic game va trang thai game | `chess.py` |
| `console/` | Logging va lenh van hanh bot | `console.py`, `command.py` |
| `database/` | Du lieu runtime, khong phai noi dat source code | SQLite, JSON, skills |

Mot so mau dang co:

- `utils/todo.py`: logic feature va JSON state; public API duoc import tu `main.py`.
- `utils/gacha_tracker.py`: SQLite va public API; hien goi `init_db()` ngay luc import.
- `utils/scheduler.py`: service chay nen, nhan client/callback tu ben goi thay vi tu quan ly toan bo bot.
- `utils/discord_ui.py`: Discord Modal/View tach khoi luong xu ly chinh.
- `utils/tool_schemas.py` + `main.execute_function()`: khai bao Gemini tool va xu ly tool call duoc quan ly rieng.

Nen uu tien cach tach logic nhu scheduler va UI nhu `discord_ui.py`. Mot vai module cu co side effect khi import; khong xem do la yeu cau bat buoc cho module moi.

## 2. Chon dung diem tich hop

Truoc khi tao file, xac dinh nguoi dung se kich hoat feature bang cach nao:

| Nhu cau | Diem vao can noi |
|---|---|
| Ham/service noi bo | Import public API vao noi su dung, thuong la `main.py` hoac service khac |
| Lenh prefix `!arona ...` | Nhanh xu ly lenh trong `main.on_message()`; cap nhat help text neu can |
| Slash command | Khai bao bang `@tree.command` trong `main.py`; `on_ready()` goi `tree.sync()` |
| Gemini function/tool | Schema trong `utils/tool_schemas.py`, handler trong `main.execute_function()`, va import public API vao `main.py` |
| Discord UI | Dinh nghia View/Modal trong module `utils/discord_ui*.py`, tao va gui no tu handler |
| Background worker | Viet coroutine start/loop co the dung; khoi dong mot lan trong nhanh startup cua `on_ready()` |
| Tinh nang co state/persistence | Chon scope user/channel/guild/global, sau do quan ly storage trong module so huu state |

Neu thay doi prompt, schema, dispatcher, quyen han hoac persistence thi doc them phan tuong ung trong [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md).

## 3. Quy trinh tao service/module

1. Tim module gan nhat ve domain va cach luu state. Dung lai helper, session manager, console logger va config hien co khi phu hop.
2. Dat file vao package co trach nhiem dung: integration/service thuong o `utils/`; persona/voice o `arona/`; game o `games/`.
3. Tao public API nho, ten ro rang, tham so va kieu tra ve on dinh. Giu chi tiet DB, HTTP va parsing ben trong module.
4. Tranh de module nghiep vu phu thuoc vao toan bo globals cua `main.py`. Truyen Discord client, callback hoac dependency can thiet tu ben goi.
5. Noi API vao entry point thich hop o muc 2. Neu can startup/shutdown, tao ham `async init/start/close` ro rang va goi dung lifecycle.
6. Them test cho logic co the chay doc lap, sau do kiem tra luong tich hop va cap nhat tai lieu neu them config, DB, command hoac tool group.

Module moi nen tranh:

- Gui Discord message truc tiep tu ham xu ly nghiep vu neu co the tra ket qua cho handler gui.
- Tao task, mo ket noi, tao DB hoac tai tai nguyen nang ngay khi import.
- Import vong `main -> utils -> main`; tach logic hoac truyen callback de pha vong phu thuoc.
- Dung `from module import *`; import ro cac public symbol can dung.

## 4. Them Gemini tool

Function calling co ba lop rieng: khai bao schema, dispatch theo ten tool va ham xu ly nghiep vu. Ca ba phai khop ten va contract.

### Tool luon co san

1. Viet logic trong module feature va xac dinh ro input/output, validation, quyen han va data scope.
2. Them function declaration vao danh sach `core_tools` trong `utils/tool_schemas.py`.
3. Them nhanh xu ly `function_name == "ten_tool"` trong `main.execute_function()` va goi public API cua module.
4. Import API vao `main.py`. Neu tool can Discord UI, dung `message` tai handler de tao/send UI; giu UI rendering trong module UI khi no lon.
5. Kiem tra tool name, parameter names/types, required fields va response format giua schema va handler.

### Tool thuoc lazy-loaded group

1. Them mo ta group vao `GROUP_DESCRIPTIONS` va tool declaration vao `_build_groups()` trong `utils/tool_schemas.py`.
2. Them handler vao `main.execute_function()` va import API can thiet.
3. `TOOL_GROUPS` duoc tao tu `_build_groups()`; `tool_groups.py` theo doi TTL cho cac group da load. Meta-tool `load_tools` hien liet ke cac group tu `GROUP_DESCRIPTIONS`.
4. Neu group phai co san trong voice session, cap nhat `VOICE_ALWAYS_ON_GROUPS` trong `get_gemini_tools()`. Neu khong, tool chi co trong text channel sau khi group duoc load.
5. Kiem tra group name, tool names va description/hints phu hop voi workflow. Khong chi them schema ma quen handler: dispatcher la bang dieu phoi thu cong.

Schema khong tu dong goi ham Python. Nguoc lai, handler khong tu dong duoc model nhin thay neu chua co declaration. Mot so module cu co schema constant rieng; neu dung lai, hay xac minh no thuc su duoc `get_gemini_tools()` su dung de tranh hai ban schema lech nhau.

### Quy tac cho tool handler

- Parse `args` co phong thu; khong tin model luon gui dung kieu theo schema.
- Kiem tra input rong, gioi han do dai/so luong, quyen va ngu canh truoc side effect.
- Lay ID scope tu `message.author`, `message.channel` hoac `message.guild`; khong de model tu chon scope nhay cam neu khong can.
- Tra ket qua ngan gon, co loi ro rang; khong lam lo token, secret, exception trace hay du lieu cua nguoi khac.
- Tool co tinh khong the dao nguoc nen co buoc xac nhan theo convention cua cac tool hien co.

## 5. Them command va Discord UI

### Prefix command

Prefix command dang duoc phan tich trong `main.on_message()`. Them mot nhanh route cu the, parse arguments, kiem tra quyen/context, xu ly loi va tra ve som de khong roi vao luong hoi thoai mac dinh. Cap nhat help text trong cung luong neu lenh duoc cong khai.

### Slash command

Slash command hien khai bao bang `@tree.command` trong `main.py`. Dung type annotation va `discord.app_commands.describe` de mo ta tham so. Nho cac rang buoc cua Discord (ten, so luong option, do dai), defer interaction neu cong viec dai, va dung followup sau khi da defer. `on_ready()` dong bo command bang `tree.sync()`.

### View/Modal

Dat component tai module UI khi co callback/state rieng hoac duoc tai su dung. Callback can kiem tra nguoi duoc phep bam, tranh set Future hai lan, xu ly timeout va vo hieu hoa UI khi hoan tat. Handler chinh nen quyet dinh luc nao gui embed/view.

## 6. State, database va external service

- Chon scope truoc: user, channel, guild hay global; dung ID tuong ung lam khoa va khong tron cac scope.
- File state/DB dat duoi `database/` va uu tien path tu `config.py` neu da co quy uoc cho data do. Tao thu muc bang `os.makedirs(..., exist_ok=True)`.
- SQLite: dung parameter binding `?`, transaction, dong connection trong `finally`/context manager, va dat unique/index constraint theo access pattern. Migration schema phai chay an toan voi DB cu.
- Khoi tao schema bang ham `init_db()` ro rang; goi tu startup hoac lazy-init co kiem soat. Tranh tao/ghi DB ngay luc import, du mot so module cu van dang lam vay.
- File JSON: xu ly file chua ton tai/hong va ghi UTF-8; can chong ghi do thi dung temporary file + atomic replace.
- HTTP async: dung `aiohttp` async, timeout va `utils.http_session` neu phu hop; dong session/tai nguyen trong shutdown. Khong block event loop bang request dong bo hoac tinh toan nang.
- Secret lay tu environment/config, khong commit vao source, khong in vao log. Validate URL, path, filename va so luong du lieu tu user/model.

`main.on_ready()` co guard `started` de cac background service chi duoc start mot lan. Cleanup/tat session nam trong shutdown path; them tai nguyen moi thi bo sung ca startup va cleanup, khong chi start.

## 7. Test va xac minh

Hien `tests/` dang trong; chua co pytest config hay test script chung trong `package.json`. Test truc tiep dang co la `utils/test_session_reuse.py`; khong gia dinh moi module da co harness.

Voi module moi, uu tien test logic pure va database tam thoi, khong can Discord token hay API that. Sau do chay smoke test cho wiring:

1. Import module va goi API voi input hop le, input sai va state rong.
2. Neu co persistence, kiem tra tao doc moi, update, reload va migration tu schema cu neu co.
3. Neu la Gemini tool, xac nhan schema xuat hien trong dung mode/group, dispatcher goi dung ham va loi input duoc xu ly.
4. Neu la Discord command/UI, test quyen, channel/DM, interaction defer/followup va timeout trong guild test.
5. Neu co background task, xac nhan khong bi start lap khi reconnect va duoc cleanup khi shutdown.

Khong goi API that trong unit test. Neu khong co test tu dong cho slice do, ghi ro smoke test can thuc hien thay vi khang dinh da test.

## 8. Checklist truoc khi hoan tat

- [ ] Module nam dung package va co public API ro rang.
- [ ] Logic khong phu thuoc globals khong can thiet; side effect/lifecycle co chu dich.
- [ ] Moi entry point da duoc noi: import, prefix/slash route, tool schema + dispatcher, hoac startup.
- [ ] Schema, argument, permission, scope va response khop nhau.
- [ ] State co path, migration, transaction va cleanup phu hop.
- [ ] Loi, timeout, empty state va input khong tin cay duoc xu ly.
- [ ] Test/check tu dong va smoke test duoc ghi nhan trung thuc.
- [ ] Cap nhat `PROJECT_DOCUMENTATION.md` neu them module quan trong, command/tool group, DB, config, env var hoac service.

## 9. Huong dan cho AI khi lam feature moi

Khi nhan yeu cau tao module, AI nen:

1. Doc file nay va [PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md), sau do xem module gan nhat ve domain va cach tich hop.
2. Truoc khi sua, xac dinh entry point, data scope, side effects, quyen han va mot check co the falsify gia thuyet.
3. Thay doi it file nhat co the hoan tat luong; khong tao plugin/registry moi neu yeu cau khong can.
4. Dong bo tat ca lop lien quan (logic, schema, dispatcher, UI, lifecycle, persistence), khong de declaration mo coi.
5. Chay check hep nhat co san, sau do bao ro file da sua va phan nao chua duoc verify.

Neu user chi can module logic, khong them Discord/Gemini wiring. Neu user can tool/command, khong dung lai module bang cach import no mot minh; phai noi vao entry point tuong ung.