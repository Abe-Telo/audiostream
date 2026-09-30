"""PC2 window: play a sender's sound, and stay in the tray when the window closes."""

from __future__ import annotations

import socket
import sys
import threading
import tkinter as tk
from tkinter import ttk

from audiostream.hub import device_argument
from audiostream.listen import Listener
from audiostream.logsetup import ensure_stdio
from audiostream.net import lan_ipv4
from audiostream.presence import PresenceService
from audiostream.roster import Roster, default_pc2_roster_path
from audiostream.tray import TrayIcon, set_window_icon


def run_pc2() -> None:
    ensure_stdio()
    app = Pc2App()
    app.run()


class Pc2App:
    def __init__(self, start_audio: bool = True, start_presence: bool = True, roster: Roster | None = None) -> None:
        self.roster = roster if roster is not None else Roster(default_pc2_roster_path())
        if roster is None:
            self.roster.load()
        self.listener = Listener()
        self._selected = list(self.roster.playback or ["default"])
        self._signature: tuple | None = None
        self.visible_names: list[str] = []
        self._presence: PresenceService | None = None
        self._tray: TrayIcon | None = None
        self._told_tray = False
        self._tone_running = False
        self._resume_after_tone = False
        self.start_audio = start_audio and sys.platform == "win32"

        self.root = tk.Tk()
        self.root.title("Audiostream PC2")
        self.root.geometry("760x780")
        self.root.minsize(640, 560)
        self.root.configure(bg="#f3f3f3")
        self._font = "Segoe UI"
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_button)
        self.root.after(250, self._tick)
        if start_presence:
            from audiostream.speakers import speaker_catalog

            self._presence = PresenceService(self.roster, socket.gethostname())
            self._presence.receiving = True
            self._presence.selected_speakers = list(self._selected)
            self._presence.speaker_catalog = speaker_catalog()
            self._presence.on_speakers = lambda ids: self.root.after(0, lambda: self._use_speakers(ids))
            self._presence.start()
        if self.start_audio:
            self.root.after(400, self.start_listening)

    def run(self) -> None:
        self.root.update_idletasks()
        set_window_icon(self.root)
        self._tray = TrayIcon(self.root, "Audiostream PC2", self._tray_items)
        self._tray.install()
        self.root.mainloop()

    def on_close_button(self) -> None:
        if self._tray is not None and self._tray.installed:
            self.root.withdraw()
            if not self._told_tray:
                self._tray.balloon(
                    "Audiostream PC2",
                    "Still playing from the tray, by the clock. Right-click the icon and choose Quit to stop.",
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
        return [("Open", self.show), ("Test tone", self.test_tone), None, ("Quit", self.quit)]

    def quit(self) -> None:
        if self._tray is not None:
            self._tray.remove()
        self.listener.stop()
        if self._presence is not None:
            self._presence.stop()
        self.root.destroy()

    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        for name, options in (
            ("TFrame", {"background": "#f3f3f3"}),
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
            text="Play another computer's sound on this PC. Closing the window leaves it in the tray.",
            wraplength=560,
        ).pack(anchor="w", pady=(2, 12))

        this_pc = ttk.LabelFrame(outer, text="This computer", padding=12)
        this_pc.pack(fill="x")
        ttk.Label(this_pc, text=socket.gethostname(), style="Card.TLabel").pack(anchor="w")
        ttk.Label(this_pc, text=lan_ipv4(), style="Muted.TLabel").pack(anchor="w")

        sound = ttk.LabelFrame(outer, text="Play on", padding=12)
        sound.pack(fill="x", pady=(12, 0))
        self.device_var = tk.StringVar()
        self.device_box = ttk.Combobox(sound, textvariable=self.device_var, state="readonly")
        self.device_box.pack(fill="x", side="left", expand=True)
        self.device_box.bind("<<ComboboxSelected>>", self._device_changed)
        ttk.Button(sound, text="Refresh", command=self._load_devices).pack(side="left", padx=(8, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=12)
        ttk.Button(controls, text="Test tone", command=self.test_tone).pack(side="left")
        self.status = ttk.Label(controls, text="Waiting for a sender.", wraplength=420)
        self.status.pack(side="left", padx=(12, 0))
        self._load_devices()

        ttk.Label(
            outer,
            text="Pair a Bluetooth speaker in Windows first. It shows up in the list above like any other speaker.",
            wraplength=560,
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            outer,
            text="Computers that are receiving show up here, on every PC running Audiostream.",
            wraplength=700,
        ).pack(side="bottom", anchor="w")

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(16, 6))
        self.count_label = ttk.Label(header, text="Receiving", font=(self._font, 11, "bold"))
        self.count_label.pack(side="left")

        list_card = tk.Frame(outer, bg="#ffffff", highlightbackground="#d0d0d0", highlightthickness=1)
        list_card.pack(fill="both", expand=True)
        self.list_frame = tk.Frame(list_card, bg="#ffffff")
        self.list_frame.pack(fill="both", expand=True)
        self.refresh_devices()

    def _load_devices(self) -> None:
        labels = ["Default playback"]
        if sys.platform == "win32":
            from audiostream.audio import AudioError
            from audiostream.win_audio import list_outputs

            try:
                for device in list_outputs():
                    labels.append(f"{device.index}  {device.name}")
            except AudioError as exc:
                self.status.configure(text=str(exc))
        self.device_box.configure(values=labels)
        if self.device_var.get() not in labels:
            self.device_var.set(labels[0])

    def _device_changed(self, _event=None) -> None:
        choice = device_argument(self.device_var.get())
        self._use_speakers(["default"] if choice is None else [choice])

    def start_listening(self) -> None:
        from audiostream.speakers import playback_arguments

        self.listener.stop()
        self.listener.start(playback_arguments(self._selected))

    def _use_speakers(self, speaker_ids) -> None:
        from audiostream.speakers import clean_speaker_ids, playback_arguments

        self._selected = clean_speaker_ids(speaker_ids) or ["default"]
        self.roster.set_playback(self._selected)
        try:
            self.roster.save()
        except OSError:
            pass
        if self._presence is not None:
            self._presence.selected_speakers = list(self._selected)
        if self.listener.running or self.start_audio:
            self.listener.stop()
            self.listener.start(playback_arguments(self._selected))

    def _edit_dialog(self, ip: str, port: int) -> None:
        from audiostream.speakers import edit_speakers_dialog, speaker_catalog

        name = ip
        catalog = [{"id": "default", "name": "Default playback"}]
        selected = ["default"]
        own = self._presence.own_ip if self._presence is not None else lan_ipv4()
        for row in self.roster.snapshot():
            if row["ip"] == ip and row["port"] == port:
                name = row["name"]
                if row.get("speakers"):
                    selected = list(row["speakers"])
        if ip == own:
            catalog = self._presence.speaker_catalog if self._presence is not None else speaker_catalog()
            selected = list(self._selected or ["default"])
        elif self._presence is not None:
            for peer in self._presence.peers():
                if peer["ip"] != ip:
                    continue
                if peer.get("speakers"):
                    catalog = peer["speakers"]
                if peer.get("selected"):
                    selected = list(peer["selected"])

        def save(speaker_ids: list[str]) -> None:
            if ip == own:
                self._use_speakers(speaker_ids)
                return
            self.roster.set_speakers(ip, port, speaker_ids)
            try:
                self.roster.save()
            except OSError:
                pass
            if self._presence is not None:
                self._presence.publish_speakers(ip, port, speaker_ids)
            self.refresh_devices()

        edit_speakers_dialog(self.root, name, catalog, selected, save)

    def refresh_devices(self) -> None:
        rows = self.roster.snapshot()
        self.visible_names = [row["name"] for row in rows]
        signature = tuple((row["ip"], row["port"], row["name"], tuple(row.get("speakers") or ())) for row in rows)
        count = len(rows)
        noun = "computer" if count == 1 else "computers"
        self.count_label.configure(text=f"Receiving  ·  {count} {noun}" if count else "Receiving")
        if signature == self._signature:
            return
        self._signature = signature
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not rows:
            tk.Label(
                self.list_frame,
                text="No computers are receiving yet.\n\nThey show up here when Audiostream is open and receiving.",
                bg="#ffffff",
                fg="#5d5d5d",
                justify="left",
                anchor="w",
                font=(self._font, 10),
                padx=14,
                pady=14,
            ).pack(fill="x")
            return
        for row in rows:
            line = tk.Frame(self.list_frame, bg="#ffffff")
            line.pack(fill="x", padx=12, pady=8)
            line.columnconfigure(0, weight=1)
            tk.Label(line, text=row["name"], bg="#ffffff", fg="#1a1a1a", font=(self._font, 11, "bold")).grid(
                row=0, column=0, sticky="w"
            )
            tk.Label(
                line,
                text=f"{row['ip']}:{row['port']}    Receiving",
                bg="#ffffff",
                fg="#5d5d5d",
                font=(self._font, 9),
            ).grid(row=1, column=0, sticky="w")
            tk.Button(
                line,
                text="Edit",
                command=lambda ip=row["ip"], port=row["port"]: self._edit_dialog(ip, port),
                relief="flat",
                bg="#ffffff",
                fg="#1a1a1a",
                font=(self._font, 9),
                cursor="hand2",
            ).grid(row=0, column=1, rowspan=2, sticky="e")
            tk.Frame(self.list_frame, bg="#eeeeee", height=1).pack(fill="x", padx=12)

    def test_tone(self) -> None:
        if self._tone_running:
            return
        if sys.platform != "win32":
            self.status.configure(text="The two beeps play on Windows.")
            return
        self._tone_running = True
        self._resume_after_tone = self.listener.running
        self.listener.stop()
        self.status.configure(text="Playing two beeps...")
        choice = device_argument(self.device_var.get())

        def work() -> None:
            try:
                from audiostream.win_audio import play_beeps

                name = play_beeps(choice)
                message = f"Played two beeps on {name}."
            except Exception as exc:
                message = str(exc)
            try:
                self.root.after(0, lambda text=message: self._tone_finished(text))
            except tk.TclError:
                return

        threading.Thread(target=work, name="audiostream-tone", daemon=True).start()

    def _tone_finished(self, message: str) -> None:
        self._tone_running = False
        self.status.configure(text=message)
        if self._resume_after_tone and self.root.winfo_exists():
            self.start_listening()

    def _tick(self) -> None:
        if not self.root.winfo_exists():
            return
        if not self._tone_running:
            with self.listener.stats.lock:
                phase = self.listener.stats.phase
                source = self.listener.stats.source
                detail = self.listener.stats.detail
                error = self.listener.stats.error
            if error:
                self.status.configure(text=error)
            elif phase == "playing" and source:
                self.status.configure(text=f"Playing from {source}.")
            elif phase == "waiting":
                text = "Waiting for a sender."
                if detail:
                    text = f"Waiting for a sender. Speaker: {detail}."
                self.status.configure(text=text)
            elif phase == "starting":
                self.status.configure(text="Starting...")
        self.refresh_devices()
        if self._tray is not None:
            with self.listener.stats.lock:
                phase = self.listener.stats.phase
            tip = "Audiostream PC2 — playing" if phase == "playing" else "Audiostream PC2"
            self._tray.set_tooltip(tip)
        try:
            self.root.after(250, self._tick)
        except tk.TclError:
            return
