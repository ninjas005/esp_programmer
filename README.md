# ESP32 Programmer

A small cross-platform GUI for flashing ESP32-family boards, built with
[Flet](https://flet.dev). It wraps `esptool.py` so you don't have to
remember serial ports, baud rates, or flash offsets by hand.


## Features

- Serial port picker with refresh, filtered to ports that look like real
  hardware (skips empty virtual `ttyS*` ports on Linux).
- Baud rate selector, defaults to `921600`.
- Non-destructive "Connect" step — checks the port is present and
  accessible without opening it, so it doesn't reset the chip or add
  latency before you're ready to flash.
- Three file rows (Bootloader / Partition Table / Application) each with
  a file picker and an editable flash address, pre-filled with the
  ESP-IDF defaults.
- Upload runs `esptool.py write_flash` in the background and streams
  live output to an in-app terminal.
- Light/dark theme toggle.
- **`?` help button** in the app bar — opens a reference table of default
  Bootloader / Partition Table / App flash offsets for ESP32, ESP32-S2,
  ESP32-S3, ESP32-C2, ESP32-C3, ESP32-C6 and ESP32-H2, since the offsets
  differ by chip and are easy to mistype.

## Requirements

- Python >= 3.14 (see `.python-version`)
- [`esptool`](https://pypi.org/project/esptool/) on `PATH` (installed as a
  dependency, exposes the `esptool.py` command used to flash)
- Linux/macOS users flashing over USB typically need to be in the
  `dialout` group: `sudo usermod -aG dialout $USER` (log out/in after).

## Install & run

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management.

```bash
uv sync
uv run python esp32_programmer.py
```

Without `uv`, a plain virtualenv works too:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python esp32_programmer.py
```

### Desktop launcher (Linux)

An `esp32-programmer.desktop` file is included so the app can be launched
from an application menu instead of a terminal. See the comments in that
file — it points at the absolute path of this checkout, so update
`Exec=` / `Icon=` if you clone the repo somewhere else. Then either
symlink or copy it in:

```bash
cp esp32-programmer.desktop ~/.local/share/applications/
```

## Working notes

- **Why "Connect" doesn't actually open the port**: fully opening a
  serial port toggles DTR/RTS on most USB-serial adapters, which resets
  ESP32 chips and adds ~500 ms of latency. `try_open_port()` just checks
  the device node exists and is readable/writable — `esptool.py` handles
  the real handshake when you flash.
- **Flash offsets differ by chip**: ESP32 and ESP32-S2 reserve the first
  4 KB of flash for the ROM bootloader header, so their bootloader lives
  at `0x1000`. Every other supported chip (S3, C2, C3, C6, H2) starts its
  1st-stage ROM loader read at `0x0`. Partition table (`0x8000`) and
  factory app (`0x10000`) offsets are the ESP-IDF defaults and are the
  same across chips unless you're using a custom partition table or
  secure boot — see the in-app `?` reference for the full table.
- **GUI toolkit version pin**: this app targets Flet 0.85.x's dialog API
  (`page.show_dialog(dialog)` / `page.pop_dialog()`). Older Flet releases
  used `page.dialog = ...` / `page.open()` / `page.close()` instead — if
  you bump the Flet version and dialogs stop opening, check that API
  first.
- **`esptool` not found errors**: the upload button catches
  `FileNotFoundError` from `subprocess.Popen` and surfaces a hint to run
  `pip install esptool` rather than failing silently.
- ESP32-C1 isn't a real Espressif part — if you're looking for it you
  probably mean C2 or C3, both of which are in the reference table.
- ESP8266 is intentionally not included in the offset table; it uses a
  different (non ESP-IDF) memory layout and isn't a target of this tool.

## License

Not yet chosen.
