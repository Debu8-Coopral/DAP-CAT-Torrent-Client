<div align="center">

# 🐱🏴‍☠️ DAP-CAT Torrent Client

**A beautiful single-file torrent client written in Python**
*Красивый торрент-клиент в одном файле на Python*

[![Version](https://img.shields.io/badge/version-1.0.2-blue.svg)](https://github.com/dap-cat/dap-cat-torrent/releases)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/platform-Windows%20x64-lightgrey.svg)](#-installation)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](#-license)
[![Languages](https://img.shields.io/badge/languages-10-orange.svg)](#-languages)
[![Themes](https://img.shields.io/badge/themes-4-9cf.svg)](#-themes)

*One Python file · 10 languages · 4 themes · per-file progress · peers & trackers*

</div>

---

## 📑 Table of Contents

- [About](#-about)
- [Features](#-features)
- [Languages](#-languages)
- [Themes](#-themes)
- [Installation](#-installation)
- [Usage](#-usage)
- [Building from Source](#-building-from-source)
- [Project Structure](#-project-structure)
- [Technical Details](#-technical-details)
- [Known Limitations](#-known-limitations)
- [Contributing](#-contributing)
- [License](#-license)

---

## 📖 About

**DAP-CAT Torrent Client** is a graphical BitTorrent client built entirely in a single
Python file using `tkinter`. It parses any standard `.torrent` file, connects to the
trackers declared inside it (with automatic fallback trackers when none are present),
shows live peer/seed statistics and downloads everything into `C:\Torrent` by default.

The whole application — protocol engine, metadata parser, GUI, translations and themes —
lives in [`torrent_client.py`](torrent_client.py). No external torrent libraries required.

---

## ✨ Features

| Feature | Description |
|---|---|
| 📦 **Single file** | Entire client in one `torrent_client.py` — easy to read, audit and build |
| 🌍 **10 languages** | Full UI localization, switchable at runtime |
| 🎨 **4 themes** | Black / Dark / Light / Hacker-Pirate, applied instantly |
| 📊 **Overall progress bar** | Live percentage + downloaded / total size of the whole torrent |
| 📄 **Per-file progress bars** | Separate progress for every file inside the torrent |
| 👥 **Peers & seeds view** | Address, port, downloaded/uploaded, speed, client ID per peer |
| 🛰️ **Auto tracker connection** | Reads `announce` + `announce-list` from the `.torrent`; falls back to built-in trackers if absent |
| 🧲 **All torrent types** | Single-file, multi-file, private torrents, v1/v2 metainfo |
| 💾 **Resume support** | Download state is saved and restored between sessions |
| 📥 **Fixed download folder** | Everything goes to `C:\Torrent` (configurable in GUI and installer) |

### Protocol implementation

The client implements the BitTorrent wire protocol directly:

```
handshake → bitfield → interested → unchoke → request → piece → have → keepalive
```

Tracker interaction is done over HTTP/HTTPS `announce` and `scrape` requests, which is
what powers the live **seeds / leechers** counters in the Trackers tab.

---

## 🌍 Languages

| Code | Language | Native name |
|---|---|---|
| `en` | English | English |
| `ru` | Russian | Русский |
| `de` | German | Deutsch |
| `it` | Italian | Italiano |
| `fr` | French | Français |
| `nl` | Dutch | Nederlands |
| `orx` | **Old Russian (pre-reform)** | Древнерусский (дореформенный) |
| `ja` | Japanese | 日本語 |
| `zh` | Chinese | 简体中文 |
| `ko` | Korean | 한국어 |

> 🏛️ The `orx` locale renders the interface in pre-reform style Russian —
> a historical curiosity included for fun.

---

## 🎨 Themes

| Theme | Style |
|---|---|
| **Black** | Deep black background with neon-green accents |
| **Dark** | Neutral charcoal, VS Code-like palette |
| **Light** | Clean bright interface |
| **Hacker-Pirate** ⚡ | Black terminal background, phosphor-green monospace text |

Themes and languages are switched from the combo boxes in the top-right corner —
changes apply instantly, no restart needed.

---

## 🚀 Installation

### Option 1 — Installer (recommended)

Download and run:

```
DAP-CAT-Torrent-Client-Setup-1.0.2.exe
```

The installer will:
1. Install the application into `Program Files\DAP-CAT Torrent Client`
2. Create the download folder **`C:\Torrent`**
3. Let you choose a custom download directory on a dedicated wizard page
4. Create a Start Menu entry and (optionally) a desktop shortcut
5. Offer to launch the app when finished
6. Support **English** and **Russian** installer languages

### Option 2 — Portable

Run the standalone executable directly, no installation needed:

```
DAP-CAT_Torrent_Client.exe
```

### Option 3 — From source

```bash
git clone https://github.com/dap-cat/dap-cat-torrent.git
cd dap-cat-torrent
pip install bencodepy
python torrent_client.py
```

Requires Python 3.10+ with `tkinter` (bundled on Windows/macOS, `sudo apt install python3-tk` on Linux).

---

## 🎮 Usage

1. Click **Add Torrent** and select a `.torrent` file
2. Confirm the download directory (default `C:\Torrent`)
3. Double-click the torrent in the list (or press **Start**) to begin downloading
4. Watch the **overall progress bar** at the top of the details pane
5. Open the **Files** tab for per-file progress
6. Open the **Peers** tab to inspect connected peers and their speeds
7. Open the **Trackers** tab to see tracker URLs and seed/leecher counts
8. Use **Pause** / **Stop** / **Remove** to control the queue

---

## 🔨 Building from Source

### Executable (PyInstaller)

```bash
pip install pyinstaller bencodepy pillow

pyinstaller --onefile --windowed ^
    --icon=bittorrent.ico ^
    --name="DAP-CAT_Torrent_Client" ^
    --add-data "bittorrent.ico;." ^
    torrent_client.py
```

Output: `dist/DAP-CAT_Torrent_Client.exe` (~10 MB)

### Installer (Inno Setup)

```powershell
& "C:\Users\<you>\AppData\Local\Programs\Inno Setup 6\ISCC.exe" installer.iss
```

Output: `installer/DAP-CAT-Torrent-Client-Setup-1.0.2.exe` (~11 MB)

This README is embedded into the installer: it is shown on the wizard's
*Information* page before installation and copied into the application folder.

---

## 📁 Project Structure

```
DAP-CAT-Torrent/
├── torrent_client.py                            # The whole client (engine + GUI + i18n + themes)
├── installer.iss                                # Inno Setup installer script
├── README.md                                    # This file (also embedded in the installer)
├── README_installer.txt                         # Installer-friendly copy of the README
├── bittorrent.ico                               # Icon: cat next to a pirate ship
├── bittorrent_preview.png                       # Icon preview
├── DAP-CAT_Torrent_Client.spec                  # PyInstaller spec
├── dist/
│   └── DAP-CAT_Torrent_Client.exe               # Portable build
└── installer/
    └── DAP-CAT-Torrent-Client-Setup-1.0.2.exe   # Windows installer
```

---

## ⚙️ Technical Details

- **Bencoding** — custom `bencode` / `bdecode` implementation plus `bencodepy` fallback
- **Info-hash** — SHA-1 of the bencoded `info` dictionary
- **Networking** — raw TCP sockets, listening port `6881` (auto-increments if busy)
- **Threading** — separate threads for the main loop, tracker announces and peer I/O
- **Storage** — pre-allocated sparse files, pieces written at correct offsets
- **GUI** — `tkinter` + `ttk` with the `clam` theme engine and dynamic `Treeview` styling
- **i18n** — flat dictionary-per-locale design, 10 locales shipped
- **Persistence** — resume data pickled to `.torrent_resume.dat` in the download folder

---

## ⚠️ Known Limitations

- The wire-protocol implementation is intentionally compact — throughput is lower than
  production clients (libtorrent / qBittorrent). Fine for demo, learning and small files.
- uTP and full DHT routing tables are not implemented; peer discovery relies on trackers
- Inbound connections may require port forwarding / firewall rules for port `6881`
- Creating `C:\Torrent` may require administrator rights on some Windows configurations

---

## 🤝 Contributing

Contributions are welcome! Feel free to:

- 🐛 Open an [issue](https://github.com/dap-cat/dap-cat-torrent/issues) for bugs
- 💡 Propose new languages, themes or protocol features
- 🔧 Submit a pull request — keep everything inside `torrent_client.py`

```bash
git checkout -b feature/my-feature
# ... edit torrent_client.py ...
python -m py_compile torrent_client.py
```

---

## 🙏 Acknowledgements

- [BitTorrent protocol specification (BEP-3)](https://www.bittorrent.org/beps/bep_0003.html)
- [Tracker protocol extension](https://www.bittorrent.org/beps/bep_0023.html)
- [Inno Setup](https://jrsoftware.org/isinfo.php) by Jordan Russell
- [PyInstaller](https://pyinstaller.org)
- Everyone who ever wanted a cat to captain a pirate ship 🐱🏴‍☠️

---

## 📄 License

MIT License — Copyright (c) 2026 DAP-CAT Team

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

<div align="center">

**Made with ❤️ and 🐱 by the DAP-CAT Team**

[⬆ Back to top](#-dap-cat-torrent-client)

</div>
