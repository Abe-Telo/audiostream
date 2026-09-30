"""Audiostream Room: one Python window. This computer sends or plays."""

from __future__ import annotations

import socket
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from audiostream.bt_win import BtError, disconnect, list_playback, nearby_bluetooth, pair, set_mute, set_volume
from audiostream.hub import StreamHub, device_argument
from audiostream.listen import Listener
from audiostream.logsetup import ensure_stdio
from audiostream.net import lan_ipv4
from audiostream.room import Room, RoomNet
from audiostream.roster import Roster
from audiostream.tray import TrayIcon, set_window_icon


def run_room() -> None:
    ensure_stdio()
    RoomApp().run()


class RoomApp:
    def __init__(self, role: str | None = None, start_network: bool = True) -> None:
        self.room = Room(socket.gethostname())
        self.roster = Roster(None)
        self.hub = StreamHub(self.roster)
        self.listener = Listener(buffer_ms=300, conceal=True)
        self.net: RoomNet | None = None
        self.start_network = start_network
        self._tray: TrayIcon | None = None
        self._told_tray = False
        self._local_devices: list[dict] = []
        self._device_error = ""
        self._dev_signature: tuple | None = None
        self._stop = threading.Event()
        self._device_thread: threading.Thread | None = None
        self.role_label_text = ""

        self.root = tk.Tk()
        self.root.title("Audiostream Room")
        self.root.geometry("760x760")
        self.root.minsize(640, 560)
        self.root.configure(bg="#f3f3f3")
        self._font = "Segoe UI"
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close_button)
        if role in {"sender", "receiver"}:
            self._apply_role(role)
        elif role is None:
            self.root.after(150, self._ask_role)
        self.root.after(400, self._tick)

    def run(self) -> None:
        self.root.update_idletasks()
        set_window_icon(self.root)
        self._tray = TrayIcon(self.root, "Audiostream Room", self._tray_items)
        self._tray.install()
        if self.start_network:
            self.net = RoomNet(self.room, on_yield=self._yield_later, on_command=self._command_later)
            self.net.start()
            if self.net.error:
                self.room.last_event = self.net.error
        self._device_thread = threading.Thread(target=self._device_loop, name="audiostream-devices", daemon=True)
        self._device_thread.start()
        self.root.mainloop()

    def on_close_button(self) -> None:
        if self._tray is not None and self._tray.installed:
            self.root.withdraw()
            if not self._told_tray:
                self._tray.balloon(
                    "Audiostream Room",
                    "Still in the tray, by the clock. Right-click the icon and choose Quit to stop.",
                )
                self._told_tray = True
            return
        self.quit()

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()

    def _tray_items(self):
        return [("Open", self.show), ("Send audio", self.send_audio), None, ("Quit", self.quit)]

    def quit(self) -> None:
        self._stop.set()
        if self._tray is not None:
            self._tray.remove()
        self.hub.stop()
        self.listener.stop()
        if self.net is not None:
            self.net.stop()
        self.root.destroy()

    def _build(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            style.theme_use("clam")
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="Audiostream Room", font=(self._font, 20)).pack(anchor="w")
        ttk.Label(
            outer,
            text="Everyone on this network can hear one computer. Closing the window leaves the app in the tray.",
            wraplength=700,
        ).pack(anchor="w", pady=(2, 8))

        self.who = ttk.Label(outer, text=f"{self.room.name}   {lan_ipv4()}", font=(self._font, 11))
        self.who.pack(anchor="w")
        self.role_label = ttk.Label(outer, text="Choose sender or receiver.", font=(self._font, 11, "bold"))
        self.role_label.pack(anchor="w", pady=(4, 8))

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x")
        self.send_button = ttk.Button(buttons, text="Send audio", command=self.send_audio)
        self.send_button.pack(side="left")
        ttk.Button(buttons, text="Play audio", command=self.play_audio).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Test tone", command=self.test_tone).pack(side="left", padx=(8, 0))

        play = ttk.LabelFrame(outer, text="Play on", padding=10)
        play.pack(fill="x", pady=(12, 0))
        self.device_var = tk.StringVar(value="Default playback")
        self.device_box = ttk.Combobox(play, textvariable=self.device_var, state="readonly")
        self.device_box.pack(fill="x", side="left", expand=True)
        self.device_box.bind("<<ComboboxSelected>>", lambda _event: self._restart_playback())
        ttk.Button(play, text="Refresh", command=self._load_outputs).pack(side="left", padx=(8, 0))
        self._load_outputs()

        self.status = ttk.Label(outer, text="48 kHz stereo. A 300 ms buffer keeps Wi-Fi drops from clicking.", wraplength=700)
        self.status.pack(anchor="w", pady=(10, 6))

        ttk.Label(outer, text="Computers in the room", font=(self._font, 11, "bold")).pack(anchor="w")
        self.pool = tk.Text(outer, height=5, wrap="word", relief="flat", bg="#ffffff")
        self.pool.pack(fill="x", pady=(4, 8))
        self.pool.configure(state="disabled")

        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Speakers and Bluetooth", font=(self._font, 11, "bold")).pack(side="left")
        ttk.Button(header, text="Pair a Bluetooth device", command=self.pair_bluetooth).pack(side="right")

        list_card = tk.Frame(outer, bg="#ffffff", highlightbackground="#d0d0d0", highlightthickness=1)
        list_card.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas = tk.Canvas(list_card, bg="#ffffff", highlightthickness=0, height=240)
        self.device_frame = tk.Frame(self.canvas, bg="#ffffff")
        self.canvas.create_window((0, 0), window=self.device_frame, anchor="nw", tags="inner")
        self.device_frame.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure("inner", width=event.width))
        self.canvas.pack(fill="both", expand=True)

    def _ask_role(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Audiostream Room")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=18)
        frame.pack()
        ttk.Label(frame, text="Is this computer the receiver or the sender?", font=(self._font, 12)).pack(anchor="w")
        ttk.Label(
            frame,
            text="Only one computer sends at a time. The others play that sound, including on Bluetooth.",
            wraplength=420,
        ).pack(anchor="w", pady=(6, 12))
        row = ttk.Frame(frame)
        row.pack(anchor="e")
        ttk.Button(row, text="Receiver", command=lambda: self._close_choice(dialog, "receiver")).pack(side="right")
        ttk.Button(row, text="Sender", command=lambda: self._close_choice(dialog, "sender")).pack(side="right", padx=(0, 8))
        dialog.protocol("WM_DELETE_WINDOW", self.quit)
        dialog.grab_set()
        dialog.update_idletasks()
        dialog.geometry(f"+{self.root.winfo_rootx() + 60}+{self.root.winfo_rooty() + 80}")

    def _close_choice(self, dialog, role: str) -> None:
        dialog.destroy()
        if role == "sender":
            self.root.after(800, self.send_audio)
        else:
            self.play_audio()

    def send_audio(self) -> None:
        other = self.room.other_sender_name()
        if other:
            sure = messagebox.askyesno(
                "Send audio",
                f"Are you sure you want to start sending? This will stop music from {other}.",
                parent=self.root,
            )
            if not sure:
                return
        self._apply_role("sender")

    def play_audio(self) -> None:
        self._apply_role("receiver")

    def _apply_role(self, role: str) -> None:
        if role == "sender":
            self.listener.stop()
            self.room.choose("sender")
            self._sync_roster()
            if not self.hub.running:
                self.hub.start(None)
            self.role_label_text = "Sending from this computer."
        else:
            self.hub.stop()
            self.room.choose("receiver")
            self._restart_playback()
            self.role_label_text = "Playing the room."
        self.role_label.configure(text=self.role_label_text)

    def _restart_playback(self) -> None:
        if self.room.role != "receiver":
            return
        self.listener.stop()
        self.listener.start(device_argument(self.device_var.get()))

    def _yield_later(self) -> None:
        try:
            self.root.after(0, self._yielded)
        except tk.TclError:
            pass

    def _yielded(self) -> None:
        self.hub.stop()
        self._restart_playback()
        self.role_label.configure(text=self.room.last_event or "Another computer is sending.")

    def _command_later(self, payload: dict) -> None:
        try:
            if payload.get("op") == "volume":
                set_volume(str(payload.get("id") or ""), float(payload.get("value") or 0))
            elif payload.get("op") == "mute":
                set_mute(str(payload.get("id") or ""), bool(payload.get("value")))
            elif payload.get("op") == "disconnect":
                disconnect(str(payload.get("device") or ""))
            elif payload.get("op") == "pair":
                pair(int(payload.get("address") or 0))
        except Exception as exc:
            self.room.last_event = str(exc)

    def _sync_roster(self) -> None:
        want = set(self.room.destinations())
        have = {(item.ip, item.port) for item in self.roster.destinations()}
        for ip, port in want - have:
            try:
                self.roster.upsert(ip, port, ip, "network", persist=False, announce=False)
            except Exception:
                continue
        for item in self.roster.destinations():
            if (item.ip, item.port) not in want:
                self.roster.remove(item.ip, item.port)

    def _load_outputs(self) -> None:
        labels = ["Default playback"]
        if sys.platform == "win32":
            from audiostream.audio import AudioError
            from audiostream.win_audio import list_outputs

            try:
                for device in list_outputs():
                    labels.append(f"{device.index}  {device.name}")
            except AudioError as exc:
                self.room.last_event = str(exc)
        self.device_box.configure(values=labels)
        if self.device_var.get() not in labels:
            self.device_var.set(labels[0])

    def _device_loop(self) -> None:
        while not self._stop.is_set():
            try:
                devices = list_playback()
                error = ""
            except Exception as exc:
                devices = []
                error = str(exc)
            self._local_devices = devices
            self._device_error = error
            if self.net is not None:
                self.net.set_devices(devices)
            self._stop.wait(3.0)

    def test_tone(self) -> None:
        if sys.platform != "win32":
            self.status.configure(text="The two beeps play on Windows.")
            return
        choice = device_argument(self.device_var.get())

        def work() -> None:
            was_receiver = self.room.role == "receiver"
            self.listener.stop()
            try:
                from audiostream.win_audio import play_beeps

                name = play_beeps(choice)
                text = f"Played two beeps on {name}."
            except Exception as exc:
                text = str(exc)
            try:
                self.root.after(0, lambda message=text, resume=was_receiver: self._tone_done(message, resume))
            except tk.TclError:
                return

        threading.Thread(target=work, daemon=True).start()

    def _tone_done(self, message: str, resume: bool) -> None:
        self.status.configure(text=message)
        if resume:
            self._restart_playback()

    def pair_bluetooth(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Pair a Bluetooth device")
        dialog.transient(self.root)
        frame = ttk.Frame(dialog, padding=12)
        frame.pack(fill="both", expand=True)
        label = ttk.Label(frame, text="Searching nearby devices...")
        label.pack(anchor="w")
        box = tk.Listbox(frame, width=48, height=8)
        box.pack(fill="both", expand=True, pady=8)
        found: list[dict] = []

        def finish(devices: list[dict], error: str) -> None:
            found.extend(devices)
            if error:
                label.configure(text=error)
            elif not devices:
                label.configure(text="No nearby Bluetooth devices.")
            else:
                label.configure(text="Select a device and pair it to this computer.")
            for device in devices:
                box.insert("end", device["name"])

        def scan() -> None:
            try:
                devices = nearby_bluetooth()
                error = ""
            except Exception as exc:
                devices = []
                error = str(exc)
            try:
                self.root.after(0, lambda: finish(devices, error))
            except tk.TclError:
                return

        def do_pair() -> None:
            selection = box.curselection()
            if not selection:
                return
            device = found[selection[0]]
            try:
                pair(int(device["address"]))
                messagebox.showinfo("Bluetooth", f"Paired {device['name']}.", parent=dialog)
            except Exception as exc:
                messagebox.showerror("Bluetooth", str(exc), parent=dialog)

        ttk.Button(frame, text="Pair to this computer", command=do_pair).pack(anchor="e")
        threading.Thread(target=scan, daemon=True).start()

    def _control(self, computer: str, op: str, **fields) -> None:
        if computer == self.room.name:
            try:
                if op == "volume":
                    set_volume(str(fields.get("id") or ""), float(fields.get("value") or 0))
                elif op == "mute":
                    set_mute(str(fields.get("id") or ""), bool(fields.get("value")))
                elif op == "disconnect":
                    disconnect(str(fields.get("device") or ""))
            except (BtError, Exception) as exc:
                self.status.configure(text=str(exc))
            return
        if self.net is None:
            self.status.configure(text="Not connected to the room yet.")
            return
        self.net.send_command(computer, op, **fields)

    def _refresh_pool(self) -> None:
        lines = []
        if self.room.role == "sender":
            lines.append(f"{self.room.name}  —  sending")
        else:
            lines.append(f"{self.room.name}  —  playing")
        sender = self.room.other_sender_name()
        for peer in self.room.peers():
            mark = "sending" if peer.get("sending") else "in the room"
            lines.append(f"{peer['name']}  ({peer['ip']})  —  {mark}")
        if sender and self.room.role != "sender":
            lines.append(f"Hearing {sender}.")
        text = "\n".join(lines) if lines else "No other computers yet. Open Audiostream Room on them."
        self.pool.configure(state="normal")
        self.pool.delete("1.0", "end")
        self.pool.insert("1.0", text)
        self.pool.configure(state="disabled")

    def _refresh_devices(self) -> None:
        groups = self.room.device_groups(self._local_devices)
        signature = tuple((name, tuple(item["id"] for item in items)) for name, items in groups)
        if signature == self._dev_signature:
            return
        self._dev_signature = signature
        self._building_devices = True
        try:
            for child in self.device_frame.winfo_children():
                child.destroy()
            if not any(items for _name, items in groups):
                text = self._device_error or "No speakers yet. Pair a Bluetooth device, or connect one in Windows."
                tk.Label(
                    self.device_frame,
                    text=text,
                    bg="#ffffff",
                    fg="#5d5d5d",
                    wraplength=640,
                    justify="left",
                ).pack(anchor="w", padx=10, pady=10)
                return
            for computer, items in groups:
                tk.Label(
                    self.device_frame,
                    text=computer,
                    bg="#ffffff",
                    fg="#1a1a1a",
                    font=(self._font, 10, "bold"),
                ).pack(anchor="w", padx=10, pady=(8, 2))
                if not items:
                    tk.Label(self.device_frame, text="No speakers reported.", bg="#ffffff", fg="#5d5d5d").pack(anchor="w", padx=16)
                    continue
                for item in items:
                    self._device_row(computer, item)
        finally:
            self._building_devices = False

    def _device_row(self, computer: str, item: dict) -> None:
        row = tk.Frame(self.device_frame, bg="#ffffff")
        row.pack(fill="x", padx=16, pady=4)
        kind = "Bluetooth" if item.get("bluetooth") else "Speaker"
        tk.Label(row, text=f"{item['name']}  ({kind})", bg="#ffffff").pack(anchor="w")
        scale = tk.Scale(
            row,
            from_=0,
            to=100,
            orient="horizontal",
            showvalue=True,
            bg="#ffffff",
            highlightthickness=0,
            command=lambda value, comp=computer, ident=item["id"]: self._volume(comp, ident, value),
        )
        scale.set(int(float(item.get("volume", 0)) * 100))
        scale.pack(fill="x")
        mute_text = "Unmute" if item.get("muted") else "Mute"
        tk.Button(
            row,
            text=mute_text,
            command=lambda comp=computer, ident=item["id"], muted=not item.get("muted"): self._control(comp, "mute", id=ident, value=muted),
        ).pack(side="left")
        if item.get("bluetooth"):
            tk.Button(
                row,
                text="Disconnect",
                command=lambda comp=computer, device=item["name"]: self._disconnect(comp, device),
            ).pack(side="left", padx=(8, 0))

    def _volume(self, computer: str, device_id: str, value: str) -> None:
        if getattr(self, "_building_devices", False):
            return
        self._pending_volume = (computer, device_id, max(0.0, min(1.0, int(float(value)) / 100)))

    def _disconnect(self, computer: str, device: str) -> None:
        if not messagebox.askyesno(
            "Disconnect",
            f"Disconnect {device}? It will unpair from {computer} until someone pairs it again.",
            parent=self.root,
        ):
            return
        self._control(computer, "disconnect", device=device)

    def _tick(self) -> None:
        if not self.root.winfo_exists():
            return
        pending = getattr(self, "_pending_volume", None)
        if pending is not None:
            self._pending_volume = None
            computer, device_id, level = pending
            self._control(computer, "volume", id=device_id, value=level)
        if self.room.role == "sender":
            self._sync_roster()
            with self.hub.stats.lock:
                running = self.hub.stats.running
                error = self.hub.stats.error
                peak = self.hub.stats.peak
            if error and not running:
                self.status.configure(text=error)
            elif running:
                count = len(self.roster.destinations())
                self.status.configure(text=f"Sending 48 kHz stereo to {count} computer(s). Level {peak:.2f}.")
        else:
            with self.listener.stats.lock:
                phase = self.listener.stats.phase
                source = self.listener.stats.source
                error = self.listener.stats.error
            if error:
                self.status.configure(text=error)
            elif phase == "playing" and source:
                self.status.configure(text=f"Playing 48 kHz stereo from {source}.")
            elif self.room.role == "receiver":
                who = self.room.other_sender_name()
                self.status.configure(text=f"Waiting for {who}." if who else "Waiting for a sender.")
        if self.room.last_event and self.room.role != "sender":
            self.role_label.configure(text=self.room.last_event)
        elif self.role_label_text:
            self.role_label.configure(text=self.role_label_text)
        self._refresh_pool()
        self._refresh_devices()
        if self._tray is not None:
            tip = "Audiostream Room — sending" if self.room.role == "sender" else "Audiostream Room"
            self._tray.set_tooltip(tip)
        try:
            self.root.after(400, self._tick)
        except tk.TclError:
            return
