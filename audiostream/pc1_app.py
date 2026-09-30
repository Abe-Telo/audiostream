"""PC1 window: send this computer's sound to as many other PCs as you add."""

from __future__ import annotations

import socket
import time as time_module
import tkinter as tk
from tkinter import messagebox, ttk

from audiostream.hub import StreamHub, device_argument
from audiostream.listen import Listener
from audiostream.logsetup import ensure_stdio
from audiostream.net import DEFAULT_PORT, lan_ipv4
from audiostream.presence import CONTROL_PORT, JoinListener, PresenceService, ReceiverWatch, SenderBeacon
from audiostream.roster import Roster, default_roster_path
from audiostream.tray import TrayIcon, set_window_icon


def run_pc1() -> None:
    ensure_stdio()
    app = Pc1App()
    app.run()


class Pc1App:
    def __init__(self, roster: Roster | None = None, start_network: bool = True) -> None:
        self.roster = roster if roster is not None else Roster(default_roster_path())
        if roster is None:
            self.roster.load()
        self.hub = StreamHub(self.roster)
        self.listener = Listener()
        self._want_stream = False
        self._want_listen = False
        self._signature: tuple | None = None
        self.visible_names: list[str] = []
        self._capture_note = ""
        self._joins: JoinListener | None = None
        self._watch: ReceiverWatch | None = None
        self._beacon: SenderBeacon | None = None
        self._presence: PresenceService | None = None
        self._tray: TrayIcon | None = None
        self._told_tray = False

        self.root = tk.Tk()
        self.root.title("Audiostream PC1")
        self.root.geometry("760x780")
        self.root.minsize(640, 560)
        self.root.configure(bg="#f3f3f3")
        self._font = "Segoe UI"
        self._build()
        if start_network:
            self._start_network()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_button)
        self.root.after(250, self._tick)

    def run(self) -> None:
        self.root.update_idletasks()
        set_window_icon(self.root)
        self._tray = TrayIcon(self.root, "Audiostream PC1", self._tray_items)
        self._tray.install()
        self.root.mainloop()

    def on_close_button(self) -> None:
        if self._tray is not None and self._tray.installed:
            self.root.withdraw()
            if not self._told_tray:
                self._tray.balloon(
                    "Audiostream PC1",
                    "Still running in the tray, by the clock. Right-click the icon and choose Quit to stop.",
                )
                self._told_tray = True
            return
        self.quit()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.focus_force()
        except tk.TclError:
            pass

    def _tray_items(self):
        from audiostream.startup import set_startup, startup_enabled

        return [
            ("Open", self.show),
            ("check", "Add to startup", startup_enabled, set_startup),
            ("scale", "Volume", self.roster.master_volume_value, self._tray_volume),
            None,
            ("Quit", self.quit),
        ]

    def _tray_volume(self, value) -> None:
        self._on_master_volume(value)
        try:
            if self.master_scale.winfo_exists():
                from audiostream.roster import clamp_volume

                self.master_scale.set(clamp_volume(value))
        except tk.TclError:
            pass

    def quit(self) -> None:
        if self._tray is not None:
            self._tray.remove()
        self.close()

    def close(self) -> None:
        self._want_stream = False
        self._want_listen = False
        self.hub.stop()
        self.listener.stop()
        for service in (self._joins, self._watch, self._beacon, self._presence):
            if service is not None:
                service.stop()
        try:
            self.roster.save()
        except OSError:
            pass
        self.root.destroy()

    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        for name, options in (
            ("TFrame", {"background": "#f3f3f3"}),
            ("Card.TFrame", {"background": "#ffffff"}),
            ("TLabel", {"background": "#f3f3f3", "foreground": "#1a1a1a", "font": (self._font, 10)}),
            ("Card.TLabel", {"background": "#ffffff", "foreground": "#1a1a1a", "font": (self._font, 10)}),
            ("Muted.TLabel", {"background": "#ffffff", "foreground": "#5d5d5d", "font": (self._font, 9)}),
            ("Title.TLabel", {"background": "#f3f3f3", "foreground": "#1a1a1a", "font": (self._font, 20)}),
            ("TButton", {"font": (self._font, 10), "padding": (10, 6)}),
            ("TLabelframe", {"background": "#ffffff"}),
            ("TLabelframe.Label", {"background": "#f3f3f3", "foreground": "#1a1a1a", "font": (self._font, 10, "bold")}),
        ):
            try:
                style.configure(name, **options)
            except tk.TclError:
                pass

        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Audiostream", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="Send this computer's sound to other PCs on your network. Add as many as you want.",
        ).pack(anchor="w", pady=(2, 12))

        this_pc = ttk.LabelFrame(outer, text="This computer", padding=12)
        this_pc.pack(fill="x")
        ttk.Label(this_pc, text=socket.gethostname(), style="Card.TLabel").pack(anchor="w")
        self.address_label = ttk.Label(this_pc, text=lan_ipv4(), style="Muted.TLabel")
        self.address_label.pack(anchor="w")

        sound = ttk.LabelFrame(outer, text="Sound to send", padding=12)
        sound.pack(fill="x", pady=(12, 0))
        self.capture_var = tk.StringVar()
        self.capture_box = ttk.Combobox(sound, textvariable=self.capture_var, state="readonly")
        self.capture_box.pack(fill="x", side="left", expand=True)
        ttk.Button(sound, text="Refresh", command=self._load_capture_devices).pack(side="right", padx=(8, 0))
        self.master_percent = ttk.Label(sound, text=f"{self.roster.master_volume}%", style="Card.TLabel", width=5)
        self.master_percent.pack(side="right")
        self.master_scale = tk.Scale(
            sound,
            from_=0,
            to=100,
            orient="horizontal",
            showvalue=False,
            length=120,
            sliderlength=16,
            width=12,
            bg="#ffffff",
            highlightthickness=0,
            troughcolor="#d0d0d0",
        )
        self.master_scale.set(self.roster.master_volume)
        self.master_scale.configure(command=self._on_master_volume)
        self.master_scale.bind("<ButtonRelease-1>", lambda _event: self._save_roster())
        self.master_scale.pack(side="right", padx=(6, 4))
        ttk.Label(sound, text="Volume", style="Card.TLabel").pack(side="right", padx=(12, 0))
        self._load_capture_devices()

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=12)
        self.start_button = ttk.Button(controls, text="Start sending", command=self._toggle)
        self.start_button.pack(side="left")
        self.receiver_button = ttk.Button(controls, text="Receiver", command=self._toggle_receiver)
        self.receiver_button.pack(side="left", padx=(8, 0))
        self.state_label = ttk.Label(controls, text="Not sending.", wraplength=360)
        self.state_label.pack(side="left", padx=(12, 0))

        meter_row = ttk.Frame(outer)
        meter_row.pack(fill="x")
        ttk.Label(meter_row, text="Level").pack(side="left")
        self.meter = tk.Canvas(meter_row, height=12, bg="#e6e6e6", highlightthickness=0)
        self.meter.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.meter_fill = self.meter.create_rectangle(0, 0, 0, 12, fill="#0f7b0f", width=0)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(16, 6))
        self.count_label = ttk.Label(header, text="Other computers", font=(self._font, 11, "bold"))
        self.count_label.pack(side="left")
        ttk.Button(header, text="Add computer", command=self._add_dialog).pack(side="right")

        ttk.Label(
            outer,
            text=(
                "A receiver on this network adds itself. You can also type its IP address. "
                "Click a name to rename it. The top volume is for every computer, and each row has its own."
            ),
            wraplength=700,
        ).pack(side="bottom", anchor="w")
        self.event_label = ttk.Label(outer, text="")
        self.event_label.pack(side="bottom", anchor="w", pady=(8, 0))

        list_card = tk.Frame(outer, bg="#ffffff", highlightbackground="#d0d0d0", highlightthickness=1)
        list_card.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(list_card, bg="#ffffff", height=220, highlightthickness=0)
        scroll = ttk.Scrollbar(list_card, orient="vertical", command=self.canvas.yview)
        self.list_frame = tk.Frame(self.canvas, bg="#ffffff")
        self.list_frame.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw", tags="inner")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure("inner", width=event.width))
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda _event: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind("<Button-5>", lambda _event: self.canvas.yview_scroll(1, "units"))
        self.refresh_devices()

    def _wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), "units")

    def _load_capture_devices(self) -> None:
        from audiostream.audio import AudioError, list_loopback_devices

        labels = ["Default playback"]
        try:
            for device in list_loopback_devices():
                labels.append(f"{device.index}  {device.name}")
        except AudioError as exc:
            self.capture_var.set("Default playback")
            self.capture_box.configure(values=labels)
            self._capture_note = str(exc)
            return
        self._capture_note = ""
        self.capture_box.configure(values=labels)
        if not self.capture_var.get():
            self.capture_var.set(labels[0])

    def _start_network(self) -> None:
        self._joins = JoinListener(self.roster, CONTROL_PORT)
        self._watch = ReceiverWatch(self.roster)
        self._joins.start()
        self._watch.start()
        if self._joins.error:
            self.roster.last_event = self._joins.error
        elif self._watch.error:
            self.roster.last_event = self._watch.error
        if not self._joins.error:
            self._beacon = SenderBeacon(socket.gethostname(), control_port=self._joins.bound_port)
            self._beacon.start()
        self._presence = PresenceService(self.roster, socket.gethostname())
        self._presence.start()
        if self._presence.error and not self.roster.last_event:
            self.roster.last_event = self._presence.error

    def _toggle(self) -> None:
        if self._want_stream:
            self._want_stream = False
            self.hub.stop()
            self.capture_box.configure(state="readonly")
            return
        self._want_stream = True
        self.capture_box.configure(state="disabled")
        with self.hub.stats.lock:
            self.hub.stats.error = ""
        self.hub.start(device_argument(self.capture_var.get()))

    def _toggle_receiver(self) -> None:
        if self._want_listen:
            self._want_listen = False
            self.listener.stop()
            self.receiver_button.configure(text="Receiver")
            return
        self._want_listen = True
        self.receiver_button.configure(text="Stop receiving")
        self.listener.stop()
        self.listener.start(None)

    def _save_roster(self) -> None:
        try:
            self.roster.save()
        except OSError:
            pass

    def _volume_released(self, ip: str, port: int) -> None:
        self._save_roster()
        if self._presence is None:
            return
        for row in self.roster.snapshot():
            if row["ip"] == ip and row["port"] == port:
                self._presence.publish_volume(ip, port, row["volume"])
                return

    def _publish(self) -> None:
        if self._presence is not None:
            self._presence.publish_local_changes()

    def _on_master_volume(self, value) -> None:
        from audiostream.roster import clamp_volume

        level = clamp_volume(value)
        self.roster.set_master_volume(level)
        if hasattr(self, "master_percent"):
            self.master_percent.configure(text=f"{level}%")

    def _on_device_volume(self, ip: str, port: int, value, label) -> None:
        from audiostream.roster import clamp_volume

        level = clamp_volume(value)
        self.roster.set_volume(ip, port, level)
        label.configure(text=f"{level}%")

    def _rename_dialog(self, ip: str, port: int) -> None:
        current = ""
        for row in self.roster.snapshot():
            if row["ip"] == ip and row["port"] == port:
                current = row["name"]
                break
        dialog = tk.Toplevel(self.root)
        dialog.title("Rename computer")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg="#f3f3f3")
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Name").grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=current)
        entry = ttk.Entry(frame, textvariable=name_var, width=32)
        entry.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        def submit() -> None:
            try:
                self.roster.rename(ip, port, name_var.get())
            except Exception as exc:
                messagebox.showerror("Rename computer", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_devices()
            self._publish()

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, sticky="e")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=submit).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        entry.focus_set()
        entry.select_range(0, "end")
        dialog.update_idletasks()
        dialog.geometry(f"+{self.root.winfo_rootx() + 80}+{self.root.winfo_rooty() + 120}")

    def _add_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Add computer")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg="#f3f3f3")
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Computers running Audiostream", font=(self._font, 10, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        status_var = tk.StringVar(value="Looking for computers on this network...")
        ttk.Label(frame, textvariable=status_var).grid(row=1, column=0, sticky="w", pady=(2, 6))
        list_wrap = tk.Frame(frame, bg="#ffffff", highlightbackground="#d0d0d0", highlightthickness=1)
        list_wrap.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        found = tk.Listbox(list_wrap, height=6, width=42, activestyle="dotbox", font=(self._font, 10), borderwidth=0)
        found.pack(fill="x")
        self._scan_rows: list[dict] = []
        scan_started = time_module.monotonic()
        if self._presence is not None:
            self._presence.probe()
        else:
            status_var.set("Type an IP address below.")

        def fill_scan() -> None:
            if not dialog.winfo_exists():
                return
            rows = self._presence.peers() if self._presence is not None else []
            known = {(row["ip"], row["port"]) for row in self.roster.snapshot()}
            labels = []
            for row in rows:
                mark = "  (already in the list)" if (row["ip"], row["port"]) in known else ""
                labels.append(f"{row['name']}    {row['ip']}{mark}")
            if labels != list(found.get(0, "end")):
                found.delete(0, "end")
                for label in labels:
                    found.insert("end", label)
                self._scan_rows = rows
            if self._presence is None:
                return
            if time_module.monotonic() - scan_started < 2.5:
                dialog.after(300, fill_scan)
                return
            if rows:
                status_var.set(f"Found {len(rows)}.")
            else:
                status_var.set("No other computers answered. Type an IP address below.")

        def add_selected() -> None:
            selection = found.curselection()
            if not selection:
                messagebox.showerror("Add computer", "Choose a computer from the list.", parent=dialog)
                return
            if selection[0] >= len(self._scan_rows):
                return
            row = self._scan_rows[selection[0]]
            name_var.set(row["name"])
            ip_var.set(row["ip"])
            port_var.set(str(row["port"]))
            submit()

        found.bind("<Double-Button-1>", lambda _event: add_selected())
        ttk.Button(frame, text="Add selected", command=add_selected).grid(row=3, column=0, sticky="w", pady=(0, 12))
        dialog.after(300, fill_scan)

        ttk.Label(frame, text="Or type an address").grid(row=4, column=0, sticky="w")
        ttk.Label(frame, text="Name").grid(row=5, column=0, sticky="w")
        name_var = tk.StringVar()
        ttk.Entry(frame, textvariable=name_var, width=42).grid(row=6, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(frame, text="IP address").grid(row=7, column=0, sticky="w")
        ip_var = tk.StringVar()
        ip_entry = ttk.Entry(frame, textvariable=ip_var, width=42)
        ip_entry.grid(row=8, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(frame, text="Port").grid(row=9, column=0, sticky="w")
        port_var = tk.StringVar(value=str(DEFAULT_PORT))
        ttk.Entry(frame, textvariable=port_var, width=42).grid(row=10, column=0, sticky="ew", pady=(0, 12))

        def submit() -> None:
            try:
                port = int(port_var.get().strip())
            except ValueError:
                messagebox.showerror("Add computer", "Port must be a number.", parent=dialog)
                return
            if ip_var.get().strip() == lan_ipv4():
                messagebox.showerror("Add computer", "That's this computer.", parent=dialog)
                return
            try:
                self.roster.upsert(ip_var.get(), port, name_var.get(), "manual")
            except Exception as exc:
                messagebox.showerror("Add computer", str(exc), parent=dialog)
                return
            dialog.destroy()
            self.refresh_devices()
            self._publish()

        buttons = ttk.Frame(frame)
        buttons.grid(row=11, column=0, sticky="e")
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
        ttk.Button(buttons, text="Add", command=submit).pack(side="right", padx=(0, 8))
        dialog.bind("<Return>", lambda _event: submit())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()
        ip_entry.focus_set()
        dialog.update_idletasks()
        dialog.geometry(f"+{self.root.winfo_rootx() + 80}+{self.root.winfo_rooty() + 80}")

    def refresh_devices(self) -> None:
        rows = self.roster.snapshot()
        self.visible_names = [row["name"] for row in rows]
        signature = (
            self._want_stream,
            tuple(
                (row["ip"], row["port"], row["name"], row["source"], row["online"], row["send_error"]) for row in rows
            ),
        )
        count = len(rows)
        noun = "computer" if count == 1 else "computers"
        self.count_label.configure(text=f"Other computers  ·  {count} {noun}" if count else "Other computers")
        if signature == self._signature:
            return
        self._signature = signature
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not rows:
            tk.Label(
                self.list_frame,
                text=(
                    "No other computers yet.\n\n"
                    "Click Add computer. Audiostream looks for other PCs running this program.\n"
                    "You can also type an IP address, for example 192.168.1.198."
                ),
                bg="#ffffff",
                fg="#5d5d5d",
                justify="left",
                anchor="w",
                font=(self._font, 10),
                padx=14,
                pady=14,
            ).pack(fill="x")
            return
        sending = self._want_stream
        for row in rows:
            line = tk.Frame(self.list_frame, bg="#ffffff")
            line.pack(fill="x", padx=12, pady=8)
            line.columnconfigure(0, weight=1)
            name = tk.Label(
                line,
                text=row["name"],
                bg="#ffffff",
                fg="#1a1a1a",
                font=(self._font, 11, "bold"),
                cursor="hand2",
            )
            name.grid(row=0, column=0, sticky="w")
            name.bind("<Button-1>", lambda _event, ip=row["ip"], port=row["port"]: self._rename_dialog(ip, port))
            detail = f"{row['ip']}:{row['port']}    {_row_status(row, sending)}"
            tk.Label(line, text=detail, bg="#ffffff", fg="#5d5d5d", font=(self._font, 9)).grid(
                row=1, column=0, sticky="w"
            )
            percent = tk.Label(
                line,
                text=f"{row['volume']}%",
                bg="#ffffff",
                fg="#1a1a1a",
                font=(self._font, 9),
                width=5,
                anchor="e",
            )
            scale = tk.Scale(
                line,
                from_=0,
                to=100,
                orient="horizontal",
                showvalue=False,
                length=110,
                sliderlength=16,
                width=12,
                bg="#ffffff",
                highlightthickness=0,
                troughcolor="#d0d0d0",
            )
            scale.set(row["volume"])
            scale.configure(
                command=lambda value, ip=row["ip"], port=row["port"], label=percent: self._on_device_volume(
                    ip, port, value, label
                )
            )
            scale.bind(
                "<ButtonRelease-1>",
                lambda _event, ip=row["ip"], port=row["port"]: self._volume_released(ip, port),
            )
            scale.grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 0))
            percent.grid(row=0, column=2, rowspan=2, sticky="e")
            tk.Button(
                line,
                text="Remove",
                command=lambda ip=row["ip"], port=row["port"]: self._remove(ip, port),
                relief="flat",
                bg="#ffffff",
                fg="#c42b1c",
                activeforeground="#c42b1c",
                font=(self._font, 9),
                cursor="hand2",
            ).grid(row=0, column=3, rowspan=2, sticky="e", padx=(8, 0))
            tk.Frame(self.list_frame, bg="#eeeeee", height=1).pack(fill="x", padx=12)

    def _remove(self, ip: str, port: int) -> None:
        self.roster.remove(ip, port)
        self.refresh_devices()
        self._publish()

    def _tick(self) -> None:
        if not self.root.winfo_exists():
            return
        thread = self.hub._thread
        with self.hub.stats.lock:
            running = self.hub.stats.running
            error = self.hub.stats.error
            peak = self.hub.stats.peak
            sent = self.hub.stats.sent
            capture = self.hub.stats.capture
            rate = self.hub.stats.rate
        if self._want_stream and error and thread is not None and not thread.is_alive() and not running:
            self._want_stream = False
            self.capture_box.configure(state="readonly")
        if self._want_stream:
            self.start_button.configure(text="Stop sending")
        else:
            self.start_button.configure(text="Start sending")
        if error and not running:
            text = error
        elif running:
            text = f"Sending from {capture} at {rate} Hz. {sent} packets."
        elif self._want_stream:
            text = "Starting..."
        elif self._capture_note:
            text = self._capture_note
        else:
            text = "Not sending."
        if self._want_listen:
            with self.listener.stats.lock:
                phase = self.listener.stats.phase
                listen_error = self.listener.stats.error
            self.receiver_button.configure(text="Stop receiving")
            if listen_error:
                text = f"{text} {listen_error}"
            elif phase == "playing":
                text = f"{text} Playing incoming audio."
            else:
                text = f"{text} Listening for a sender."
        else:
            self.receiver_button.configure(text="Receiver")
        self.state_label.configure(text=text)
        width = max(self.meter.winfo_width(), 1)
        level = min(1.0, max(0.0, peak))
        self.meter.coords(self.meter_fill, 0, 0, width * level, 12)
        self.event_label.configure(text=self.roster.last_event)
        if self._tray is not None:
            tip = "Audiostream PC1 — sending" if self._want_stream else "Audiostream PC1"
            self._tray.set_tooltip(tip)
        self.refresh_devices()
        try:
            self.root.after(250, self._tick)
        except tk.TclError:
            return


def _row_status(row: dict, sending: bool) -> str:
    if row["send_error"]:
        return "Can't reach this computer"
    if sending:
        return "Sending"
    if row["source"] == "network" and row["online"]:
        return "Joined from the network"
    if row["source"] == "network":
        return "Joined earlier"
    return "Added by you"
