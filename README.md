# audiostream

Stream the whole system audio mix from one PC to another over the local network, and play it on a speaker you pick — including a Bluetooth speaker or headphones the operating system already connected.

There are two roles in one package:

- **Sender (PC1)** records the desktop mix (WASAPI loopback on Windows, PulseAudio/PipeWire monitor on Linux) and sends PCM over UDP.
- **Receiver (PC2)** plays that stream on an output device you choose from a numbered list. Pair Bluetooth in the OS settings first. This app does not implement Bluetooth itself.

## Windows app

Download the two programs. They do not need a separate Python install:

- [AudiostreamPC1.exe](https://github.com/Abe-Telo/audiostream/releases/download/v0.3.0/AudiostreamPC1.exe) on the computer that is playing the sound
- [AudiostreamPC2.exe](https://github.com/Abe-Telo/audiostream/releases/download/v0.3.0/AudiostreamPC2.exe) on every computer that should hear it

Double-click the file. The window opens. When you close the window, the app stays in the tray by the clock. Right-click that icon and choose **Quit** when you want it to stop. **Open** brings the window back.

On PC1, add as many computers as you want, then click **Start sending**. On PC2, pick the speaker (Bluetooth is in that list after Windows has paired it) and click **Test tone**. You should hear two beeps. PC2 adds itself to PC1 on the same network.

If Windows Firewall asks, allow the app on private networks.

## Install

Python 3.10 or newer. On Windows the sender uses WASAPI and does not need NumPy. Run each command on its own line. Pasting two commands onto one line makes pip fail with `no such option: -m`.

```bash
python -m pip install -r requirements.txt
```

Run the commands below from this directory. After `pip install .`, the `audiostream` command is equivalent to `python -m audiostream`.

## Pair Bluetooth first (PC2)

1. Pair and connect the speaker or headphones in the system sound settings.
2. Confirm the OS can play to it (a system sound is enough).
3. It then appears as a normal output device in the list below.

## Run the receiver (PC2)

```bash
python -m audiostream receiver --list-devices
python -m audiostream receiver --device 1 --test-tone
```

`--test-tone` plays a two-second tone on the device you picked, then listens for the sender. Use it to prove the Bluetooth output works before anything is on the network.

`--device` takes the number from the list, starting at **1**, or a unique part of the name. The first output is `--device 1`. If you only have one playback device, that is device 1. With no `--device`, the receiver prints the list and asks you to type a number. If stdin is not a terminal, pass `--device`.

The receiver adds this PC to a sender running the Audiostream window on the same network. Leave that on. Use `--no-join` only if you want to type the IP on PC1 instead.

Default listen port is UDP **45123**, bound to all interfaces. The jitter buffer defaults to **120 ms**.

## PC1 window

On PC1, double-click `AudiostreamPC1.exe`, or `PC1.bat` if you are running from the source folder. Closing the window leaves PC1 in the tray.

On PC2, double-click `AudiostreamPC2.exe`, or `PC2.bat` from the source folder. Closing the window leaves PC2 in the tray and it keeps listening.

The window sends this computer's sound to every PC in the list. Add as many as you want:

- Start the receiver on another PC on the same network. It shows up on its own.
- Or click **Add computer** and type its IP address, for example `192.168.1.198`.

Choose **Start sending**. One capture is sent to every computer in the list. Remove a computer when it should stop hearing this PC. The list is saved and comes back the next time you open the window.

**Volume** next to the sound list is the level for every computer. Each computer in the list also has its own slider. Click a computer's name to rename it. **Receiver** plays incoming audio on this PC, on the default speakers.

**Add computer** looks on the network for other PCs running Audiostream and lists them. Pick one, or type an IP address. A computer you add shows up on the other PCs that are running Audiostream too. Computers that are receiving also show up on their own, on both the PC1 and PC2 windows. That uses UDP **45127**.

**Edit**, on the right of a computer, chooses which speakers play the stream. More than one speaker can be checked.

Right-click the tray icon for **Open**, **Add to startup** (a check mark when Windows will open Audiostream at sign-in), **Volume**, and **Quit**.

These controls are in the Python window (`PC1.bat` from this source). The 0.3.0 exe does not include them. If `AudiostreamPC1.exe` is sitting in the same folder as `PC1.bat`, move the exe out of that folder first, or the bat file opens the older program.

When Windows asks, allow Python on private networks. PC1 listens for other computers on UDP **45124** and **45125**. Each PC2 still receives the audio on UDP **45123**.

The command line still works when you want one destination and no window:

```bash
python -m pip install -r requirements.txt
python -m audiostream sender --list-devices
python -m audiostream sender --host 192.168.1.198
```

`--host` is the receiver's LAN address. The sender captures the system mix, not the microphone. Pick another loopback with `--device` if you need to.

To find the receiver without typing an IP, leave the receiver's discovery beacon on (the default) and start the sender with:

```bash
python -m audiostream sender --discover
```

The beacon is a UDP broadcast on port **45124**. If the network blocks broadcast, use `--host`.

## Firewall

On PC2, allow inbound UDP **45123**. On PC1, allow inbound UDP **45124** and **45125** so other computers can add themselves. On Windows, allow Python on private networks when the firewall prompt appears. On Linux, open those ports in `ufw` or firewalld if a host firewall is enabled.

If the receiver stays on `Waiting for the sender` while the sender's packet counter climbs, the packets are not arriving: wrong IP, or the firewall is dropping UDP.

## Latency

Default packets are about 5 ms of 48 kHz stereo PCM so each datagram fits in one LAN packet. The receiver holds **120 ms** before it starts playing, then drops a packet that shows up after its play time instead of stalling.

What you hear is roughly the buffer plus one packet plus the OS audio period and Wi-Fi delay, on the order of **150 ms**. That is the intended tradeoff.

- Stutters or gaps: raise the buffer, for example `--buffer-ms 200`.
- Delay feels long and the LAN is clean: try `--buffer-ms 60`.
- A `peak 0.000` line on the sender means the desktop mix is silent. Play something on PC1. A rising peak means capture is working.

Useful range on Wi-Fi is about 50–200 ms of buffer.

## Options

| Flag | Role | Default |
| --- | --- | --- |
| `--host` | sender | required, unless `--discover` |
| `--port` | both | 45123 |
| `--sample-rate` | sender (tone on receiver) | 48000 |
| `--channels` | sender (tone on receiver) | 2 |
| `--chunk-ms` | sender | 5 (capped to stay under the MTU) |
| `--buffer-ms` | receiver | 120 |
| `--device` | both | prompt, or the default output's loopback |
| `--bind` | receiver | 0.0.0.0 |
| `--test-tone` | receiver | off |
| `--no-discover` | receiver | beacon on |

The receiver follows the sample rate and channel count in the stream. `--sample-rate` and `--channels` on the receiver apply to the test tone.

## Operating systems

**Windows (primary).** Capture is WASAPI loopback of a playback device: everything that device is playing. Playback is the output you select, including a connected Bluetooth endpoint.

**Linux.** Capture is the PulseAudio or PipeWire monitor source of an output. PipeWire's Pulse compatibility socket is enough. Playback is any Pulse/PipeWire sink, including Bluetooth sinks that the desktop has already connected.

**macOS.** CoreAudio cannot record the system mix. A microphone list is not desktop audio. Install a virtual device such as BlackHole, route system output into it yourself, and pass that input with `sender --device`. This app does not create the virtual device and does not pretend the default input is the system mix.

## Tests

No sound card is required for the tests. They cover packet encode/decode, sequence reorder, late drops, gap silence, and CLI `--help`.

```bash
python -m pytest
```

Cloud VMs and headless machines usually have no audio server. `sender --list-devices` and `receiver --list-devices` then exit with an error that names PulseAudio/PipeWire or WASAPI, instead of a traceback. If a loopback device exists, record from it the same way the sender does.

## How the packets work

Each datagram is a 20-byte header (`AS01`, version, channels, sample rate, sequence, frame count) plus interleaved PCM s16le. The receiver reorders a short window, plays in sequence order, writes silence for a missing sequence, and discards a packet that arrives after the playhead has moved on. A large sequence jump (the sender restarted) resyncs the buffer.
