#!/usr/bin/env python3
"""ESP32 Programmer — cross-platform GUI flash tool for ESP32/ESP8266 devices."""

import asyncio
import flet as ft
import os
import sys
import glob
import subprocess
import threading

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


BAUD_RATES = ["921600", "460800", "230400", "115200", "74880", "57600", "38400", "19200", "9600"]
DEFAULT_BAUD = "921600"
DEFAULT_ADDRS = {
    "bootloader": "0x1000",
    "partition":  "0x8000",
    "app":        "0x10000",
}

# ESP-IDF default flash offsets per chip. Bootloader offset differs because
# ESP32 and ESP32-S2 reserve the first 4KB for the ROM bootloader header,
# while every other chip's 1st-stage ROM loader starts reading at 0x0.
# Partition table and factory app offsets come from the default partitions.csv
# and are the same across chips unless a custom partition table is used.
CHIP_ADDRS = [
    ("ESP32",    "0x1000", "0x8000", "0x10000"),
    ("ESP32-S2", "0x1000", "0x8000", "0x10000"),
    ("ESP32-S3", "0x0000", "0x8000", "0x10000"),
    ("ESP32-C2", "0x0000", "0x8000", "0x10000"),
    ("ESP32-C3", "0x0000", "0x8000", "0x10000"),
    ("ESP32-C6", "0x0000", "0x8000", "0x10000"),
    ("ESP32-H2", "0x0000", "0x8000", "0x10000"),
]


def scan_ports():
    """Return serial ports likely to have real hardware attached."""
    if HAS_SERIAL:
        all_ports = serial.tools.list_ports.comports()
        # ttyS* are kernel virtual ports — skip them unless they report a
        # real USB/PCI product so we don't flood the list with 32 empty entries.
        def is_real(p):
            if "ttyS" in p.device and sys.platform.startswith("linux"):
                return p.vid is not None or (p.description or "").lower() not in ("", "n/a")
            return True
        return sorted(p.device for p in all_ports if is_real(p))
    if sys.platform == "win32":
        return []
    ports = []
    for pat in ["/dev/ttyACM*", "/dev/ttyUSB*", "/dev/cu.usb*"]:
        ports.extend(glob.glob(pat))
    return sorted(ports)


def try_open_port(port: str, _baud: str):
    """Check that the port device is accessible without opening it.

    Fully opening the port asserts DTR/RTS which resets ESP32 chips and adds
    ~500 ms of latency.  A device-file existence check is instant and sufficient
    — esptool handles the real connection at flash time.
    """
    if sys.platform == "win32":
        # On Windows verify the port appears in the system list.
        if HAS_SERIAL:
            available = [p.device for p in serial.tools.list_ports.comports()]
            if port in available:
                return True, None
            return False, f"Port {port} not found"
        return True, None   # can't check without pyserial; assume OK
    # Linux / macOS: the device node must exist and be a character device.
    if not os.path.exists(port):
        return False, f"Device {port} not found"
    if not os.access(port, os.R_OK | os.W_OK):
        return False, f"No permission to access {port} — try: sudo usermod -aG dialout $USER"
    return True, None


def build_flash_cmd(port: str, baud: str, entries: list) -> list:
    cmd = ["esptool.py", "--port", port, "--baud", baud, "write_flash"]
    for addr, path in entries:
        cmd += [addr, path]
    return cmd


GREEN   = ft.Colors.GREEN_700
RED     = ft.Colors.RED_700
WHITE   = ft.Colors.WHITE
GREY7   = ft.Colors.GREY_700
GREY8   = ft.Colors.GREY_800
CYAN    = "#58a6ff"
ERR_C   = "#f85149"
TERM_BG = "#0d1117"


async def main(page: ft.Page):
    page.title = "ESP32 Programmer"
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 1120
    page.window.height = 730
    page.window.min_width = 880
    page.window.min_height = 600
    page.padding = 0

    ctx = {"port": None, "baud": None}

    # ── Theme toggle ──────────────────────────────────────────────────────────

    def toggle_theme(e):
        if page.theme_mode == ft.ThemeMode.DARK:
            page.theme_mode = ft.ThemeMode.LIGHT
            theme_btn.icon = ft.Icons.DARK_MODE
        else:
            page.theme_mode = ft.ThemeMode.DARK
            theme_btn.icon = ft.Icons.LIGHT_MODE
        page.update()

    theme_btn = ft.IconButton(
        icon=ft.Icons.LIGHT_MODE,
        tooltip="Toggle dark / light theme",
        on_click=toggle_theme,
    )

    # ── Help dialog — flash address reference ──────────────────────────────────

    def close_help(e):
        page.pop_dialog()

    help_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text("Flash Address Reference"),
        content=ft.Container(
            width=520,
            content=ft.Column(
                [
                    ft.Text(
                        "Default ESP-IDF flash offsets. Custom partition tables "
                        "or secure boot may change these.",
                        size=12,
                        opacity=0.7,
                    ),
                    ft.Container(height=8),
                    ft.DataTable(
                        columns=[
                            ft.DataColumn(ft.Text("Chip")),
                            ft.DataColumn(ft.Text("Bootloader")),
                            ft.DataColumn(ft.Text("Partition Table")),
                            ft.DataColumn(ft.Text("App")),
                        ],
                        rows=[
                            ft.DataRow(
                                cells=[
                                    ft.DataCell(ft.Text(chip, weight=ft.FontWeight.W_500)),
                                    ft.DataCell(ft.Text(boot, font_family="monospace")),
                                    ft.DataCell(ft.Text(part, font_family="monospace")),
                                    ft.DataCell(ft.Text(app, font_family="monospace")),
                                ]
                            )
                            for chip, boot, part, app in CHIP_ADDRS
                        ],
                    ),
                ],
                tight=True,
                scroll=ft.ScrollMode.AUTO,
            ),
        ),
        actions=[ft.TextButton("Close", on_click=close_help)],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    def show_help(e):
        page.show_dialog(help_dialog)

    help_btn = ft.IconButton(
        icon=ft.Icons.HELP_OUTLINE,
        tooltip="Flash address reference",
        on_click=show_help,
    )

    # ── LEFT PANE — Serial Connection ─────────────────────────────────────────

    port_dd = ft.Dropdown(
        label="Serial Port", hint_text="Select port…", width=176, options=[],
    )
    baud_dd = ft.Dropdown(
        label="Baud Rate", value=DEFAULT_BAUD, width=210,
        options=[ft.dropdown.Option(key=b, text=b) for b in BAUD_RATES],
    )

    conn_msg  = ft.Text("", size=12, color=WHITE)
    conn_bar  = ft.Container(
        content=conn_msg,
        padding=ft.Padding(left=10, right=10, top=8, bottom=8),
        border_radius=6, visible=False,
    )

    def set_conn_status(msg: str, ok: bool):
        conn_msg.value = ("✓  " if ok else "✗  ") + msg
        conn_bar.bgcolor = GREEN if ok else RED
        conn_bar.visible = True
        page.update()

    def refresh_ports(e=None):
        ports = scan_ports()
        port_dd.options = [ft.dropdown.Option(key=p, text=p) for p in ports]
        port_dd.value = ports[0] if ports else None
        port_dd.update()

    # ft.Button has no .text property — use a ft.Text control as content
    connect_label = ft.Text("Connect")

    def on_connect(e):
        port = port_dd.value
        if not port:
            set_conn_status("Select a serial port first.", ok=False)
            return
        connect_btn.disabled = True
        connect_label.value = "Connecting…"
        page.update()

        def _work():
            ok, err = try_open_port(port, baud_dd.value)
            if ok:
                ctx["port"] = port
                ctx["baud"] = baud_dd.value
                set_conn_status(f"Connected  {port}  @  {baud_dd.value} baud", ok=True)
                placeholder.visible = False
                right_pane.visible = True
            else:
                set_conn_status(err or "Failed to open port.", ok=False)
            connect_btn.disabled = False
            connect_label.value = "Reconnect" if ok else "Connect"
            page.update()

        threading.Thread(target=_work, daemon=True).start()

    connect_btn = ft.Button(
        content=ft.Row(
            [ft.Icon(ft.Icons.CABLE, size=18), connect_label],
            spacing=6, tight=True,
        ),
        on_click=on_connect,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
    )

    left_pane = ft.Container(
        width=258,
        border=ft.Border(right=ft.BorderSide(1, GREY7)),
        padding=16,
        content=ft.Column(
            [
                ft.Text("Serial Connection", size=16, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Container(height=8),
                ft.Row(
                    [
                        port_dd,
                        ft.IconButton(
                            icon=ft.Icons.REFRESH,
                            tooltip="Refresh port list",
                            on_click=refresh_ports,
                        ),
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.END,
                ),
                ft.Container(height=10),
                baud_dd,
                ft.Container(height=14),
                connect_btn,
                ft.Container(expand=True),
                conn_bar,
            ],
            expand=True,
            spacing=0,
        ),
    )

    # ── RIGHT PANE — Programmer ───────────────────────────────────────────────

    def file_row(label: str, hint: str, default_addr: str):
        path_tf = ft.TextField(
            label=label, hint_text=hint, read_only=True, expand=True, dense=True,
        )
        addr_tf = ft.TextField(label="Address", value=default_addr, width=116, dense=True)
        fp = ft.FilePicker()   # registered with the page after page.add() below

        async def browse(_e):
            files = await fp.pick_files(
                dialog_title=f"Select {label}",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["bin"],
            )
            if files:
                path_tf.value = files[0].path
                page.update()

        row = ft.Row(
            [
                path_tf,
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN,
                    tooltip=f"Browse for {label}",
                    on_click=browse,
                ),
                addr_tf,
            ],
            vertical_alignment=ft.CrossAxisAlignment.END,
        )
        return row, path_tf, addr_tf, fp

    boot_row, boot_path, boot_addr, boot_fp = file_row(
        "Bootloader Binary", "bootloader.bin", DEFAULT_ADDRS["bootloader"]
    )
    part_row, part_path, part_addr, part_fp = file_row(
        "Partition Table",   "partitions.bin", DEFAULT_ADDRS["partition"]
    )
    app_row,  app_path,  app_addr,  app_fp  = file_row(
        "Application Binary","firmware.bin",   DEFAULT_ADDRS["app"]
    )

    # Terminal output
    out_lv = ft.ListView(expand=True, spacing=1, auto_scroll=True)
    terminal = ft.Container(
        content=out_lv,
        bgcolor=TERM_BG,
        border_radius=6,
        border=ft.Border.all(1, GREY8),
        padding=10,
        expand=True,
    )

    def log(text: str, color=None):
        out_lv.controls.append(
            ft.Text(text, size=11, font_family="monospace", color=color, selectable=True)
        )
        page.update()

    up_msg = ft.Text("", size=12, color=WHITE)
    up_bar = ft.Container(
        content=up_msg,
        padding=ft.Padding(left=10, right=10, top=8, bottom=8),
        border_radius=6, visible=False,
    )

    def set_upload_status(msg: str, ok: bool):
        up_msg.value = ("✓  " if ok else "✗  ") + msg
        up_bar.bgcolor = GREEN if ok else RED
        up_bar.visible = True
        page.update()

    upload_label = ft.Text("Upload to ESP32")

    upload_btn = ft.Button(
        content=ft.Row(
            [ft.Icon(ft.Icons.UPLOAD, size=18), upload_label],
            spacing=6, tight=True,
        ),
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=6)),
    )

    def on_upload(e):
        entries = [
            (boot_addr.value.strip(), (boot_path.value or "").strip()),
            (part_addr.value.strip(), (part_path.value or "").strip()),
            (app_addr.value.strip(),  (app_path.value  or "").strip()),
        ]
        valid = [(a, p) for a, p in entries if a and p]
        if not valid:
            set_upload_status("Select at least one binary file before uploading.", ok=False)
            return

        cmd = build_flash_cmd(ctx["port"], ctx["baud"], valid)
        out_lv.controls.clear()
        log("$ " + " ".join(cmd), color=CYAN)
        up_bar.visible = False
        upload_btn.disabled = True
        upload_label.value = "Uploading…"
        page.update()

        def _run():
            try:
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for line in proc.stdout:
                    log(line.rstrip())
                proc.wait()
                if proc.returncode == 0:
                    set_upload_status("Upload successful!", ok=True)
                else:
                    set_upload_status(
                        f"Upload failed — esptool exited with code {proc.returncode}", ok=False
                    )
            except FileNotFoundError:
                log("esptool.py not found. Install with: pip install esptool", color=ERR_C)
                set_upload_status("esptool not found — run: pip install esptool", ok=False)
            except Exception as ex:
                log(f"Error: {ex}", color=ERR_C)
                set_upload_status(str(ex), ok=False)
            finally:
                upload_btn.disabled = False
                upload_label.value = "Upload to ESP32"
                page.update()

        threading.Thread(target=_run, daemon=True).start()

    upload_btn.on_click = on_upload

    def clear_output(e):
        out_lv.controls.clear()
        page.update()

    right_pane = ft.Container(
        expand=True,
        padding=16,
        visible=False,
        content=ft.Column(
            [
                ft.Text("Programmer", size=16, weight=ft.FontWeight.BOLD),
                ft.Divider(height=1),
                ft.Container(height=6),
                ft.Text("Bootloader Binary", size=13, weight=ft.FontWeight.W_500),
                boot_row,
                ft.Container(height=8),
                ft.Text("Partition Table", size=13, weight=ft.FontWeight.W_500),
                part_row,
                ft.Container(height=8),
                ft.Text("Application Binary", size=13, weight=ft.FontWeight.W_500),
                app_row,
                ft.Container(height=14),
                ft.Row(
                    [
                        ft.Text("Terminal Output", size=13, weight=ft.FontWeight.W_500),
                        ft.Container(expand=True),
                        ft.Button(
                            content=ft.Row(
                                [ft.Icon(ft.Icons.CLEAR_ALL, size=16), ft.Text("Clear")],
                                spacing=4, tight=True,
                            ),
                            on_click=clear_output,
                        ),
                        ft.Container(width=8),
                        upload_btn,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                terminal,
                up_bar,
            ],
            expand=True,
            spacing=4,
        ),
    )

    placeholder = ft.Container(
        expand=True,
        content=ft.Column(
            [
                ft.Icon(ft.Icons.DEVELOPER_BOARD, size=80, opacity=0.22),
                ft.Container(height=14),
                ft.Text(
                    "Connect to a serial port\nto enable the programmer",
                    text_align=ft.TextAlign.CENTER,
                    size=15,
                    opacity=0.32,
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    page.appbar = ft.AppBar(
        leading=ft.Icon(ft.Icons.DEVELOPER_BOARD),
        leading_width=48,
        title=ft.Text("ESP32 Programmer", weight=ft.FontWeight.BOLD),
        center_title=False,
        actions=[help_btn, theme_btn],
    )

    page.add(
        ft.Row(
            [left_pane, placeholder, right_pane],
            expand=True,
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
    )

    # Yield the event loop so Flutter can process the initial render before
    # we send more patches (port list + FilePicker service registrations).
    await asyncio.sleep(0.05)

    # Populate the port dropdown first — a targeted update on just port_dd.
    refresh_ports()

    # Register FilePicker services AFTER the main UI has rendered.
    for fp in (boot_fp, part_fp, app_fp):
        page._services.register_service(fp)
    page.update()


ft.run(main)
