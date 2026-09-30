"""Which speakers play the stream. More than one can be on at the same time."""

from __future__ import annotations

import sys


def speaker_catalog() -> list[dict]:
    """Playback devices on this computer. Default playback is always first."""
    rows = [{"id": "default", "name": "Default playback"}]
    if sys.platform != "win32":
        return rows
    try:
        from audiostream.win_audio import list_outputs
    except Exception:
        return rows
    try:
        for device in list_outputs():
            rows.append({"id": str(device.index), "name": device.name})
    except Exception:
        return rows
    return rows


def clean_speaker_ids(value) -> list[str]:
    if not isinstance(value, list):
        return []
    ids = []
    for item in value:
        text = str(item).strip()
        if text and text not in ids:
            ids.append(text[:32])
    return ids[:16]


def clean_speaker_catalog(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    rows = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        speaker_id = str(item.get("id") or "").strip()[:32]
        name = str(item.get("name") or speaker_id).strip()[:64]
        if not speaker_id or speaker_id in seen:
            continue
        seen.add(speaker_id)
        rows.append({"id": speaker_id, "name": name or speaker_id})
    return rows[:16]


def playback_arguments(selected: list[str]) -> list[str | None]:
    """Speaker ids for Listener.start. 'default' follows the Windows default."""
    devices: list[str | None] = []
    for speaker_id in selected or ["default"]:
        if speaker_id in ("default", ""):
            devices.append(None)
        else:
            devices.append(speaker_id)
    return devices or [None]


def edit_speakers_dialog(root, computer_name: str, catalog: list[dict], selected: list[str], on_save) -> None:
    """Checkbox list. Several speakers can play the stream together."""
    import tkinter as tk
    from tkinter import ttk

    dialog = tk.Toplevel(root)
    dialog.title("Edit")
    dialog.transient(root)
    dialog.resizable(False, False)
    dialog.configure(bg="#f3f3f3")
    frame = ttk.Frame(dialog, padding=16)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text=computer_name, font=("Segoe UI", 12, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Speakers for the stream. More than one can be on.",
        wraplength=360,
    ).pack(anchor="w", pady=(2, 10))
    chosen = set(selected or ["default"])
    variables = []
    if not catalog:
        catalog = [{"id": "default", "name": "Default playback"}]
    for item in catalog:
        variable = tk.BooleanVar(value=item["id"] in chosen)
        tk.Checkbutton(
            frame,
            text=item["name"],
            variable=variable,
            anchor="w",
            bg="#f3f3f3",
            activebackground="#f3f3f3",
            font=("Segoe UI", 10),
        ).pack(fill="x", anchor="w")
        variables.append((item["id"], variable))

    def save() -> None:
        ids = [speaker_id for speaker_id, variable in variables if variable.get()]
        if not ids:
            ids = ["default"]
        on_save(ids)
        dialog.destroy()

    buttons = ttk.Frame(frame)
    buttons.pack(fill="x", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side="right")
    ttk.Button(buttons, text="Save", command=save).pack(side="right", padx=(0, 8))
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    dialog.bind("<Return>", lambda _event: save())
    dialog.grab_set()
    dialog.update_idletasks()
    dialog.geometry(f"+{root.winfo_rootx() + 70}+{root.winfo_rooty() + 80}")
