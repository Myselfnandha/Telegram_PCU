# 🚀 Telegram Web Uploader

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Telethon](https://img.shields.io/badge/Telethon-MTProto%20v2-2CA5E0.svg?style=flat-square&logo=telegram)](https://github.com/LonamiWebs/Telethon)
[![Socket.IO](https://img.shields.io/badge/Socket.IO-Realtime%20Engine-010101.svg?style=flat-square&logo=socketdotio)](https://socket.io)
[![Tests](https://img.shields.io/badge/Tests-11%20Passed-brightgreen.svg?style=flat-square&logo=pytest)](https://pytest.org)
[![License](https://img.shields.io/badge/License-MIT-purple.svg?style=flat-square)](LICENSE)

An ultra-fast, high-performance web interface for uploading files of **any size (up to 2GB+ with automated zero-copy sequence slicing)** directly to your personal Telegram cloud, channels, groups, or direct messages using direct Telegram MTProto.

---

## ✨ Key Features

### ⚡ Turbo MTProto Multi-Worker Engine
- **Parallel Chunk Streaming**: Streams 512KB chunks concurrently over 6–8 parallel MTProto worker connections, saturating available bandwidth from **10 MB/s up to 40+ MB/s**.
- **Chunk-Level Persistence**: Upload progress is cached at the chunk level. Pausing or refreshing resumes immediately without re-uploading completed parts.
- **Linux Atomic `os.pread` I/O**: Direct lock-free reads from the Linux page cache with zero seek contention across workers.
- **Zero Disk Fragmentation (`fallocate`)**: Pre-allocates contiguous disk blocks on filesystem for incoming multi-GB transfers.

### ✂️ Zero-Copy Direct-Range Slicing (2GB+ Files)
- Files larger than 1.9GB are sliced directly via in-memory byte ranges (`byte_offset`, `byte_length`) into sequence parts (`file.part001`, `file.part002`).
- **Saves 50% SSD writes** and eliminates waiting time for disk-based splitters.

### 🛡️ Network Watchdog & Auto-Recovery
- Actively detects network drops and server reachability via continuous heartbeat checks.
- Safely auto-pauses active uploads during network blips and **seamlessly auto-resumes** when connectivity is restored.
- MTProto worker retry loops feature exponential backoff up to 6 attempts against network timeouts.

### 🎨 5 Modern Glass Themes & Customizer
- **🌌 Deep Space**: Sleek dark violet & indigo with glowing cyan accents.
- **⚡ Cyberpunk Neon**: High-contrast electric pink & neon cyan on deep obsidian.
- **❄️ Nord Frost**: Polar night slate and arctic ice blue.
- **🖤 Midnight OLED**: True pitch black (`#000000`) with emerald luminescence.
- **🌅 Sunset Glow**: Deep velvet twilight with warm golden amber gradients.
- **Live Glass Slider**: Adjust backdrop blur and frosted glass intensity in real-time with automatic `localStorage` persistence.

### 📱 Responsive 2-Column Split Workspace
- **Left Column**: Active Upload Queue with live 60fps GPU-accelerated progress meters, status-themed border accents, and batch controls (`⏸️ Pause All` / `⏹️ Stop All`).
- **Right Column**: Destination Selector Bar, Zero-Clutter Dropzone, and Adaptive Activity History Stream.

---

## 🏗️ Architecture

```
┌────────────────────────────────────────────────────────┐
│              Browser Frontend (Vanilla ES6)            │
│  - 2-Column Responsive Workspace Grid                  │
│  - Live Socket.IO Event Engine & UI State Manager       │
│  - Network Watchdog & Auto-Recovery Controller         │
│  - Theme & Glass Aesthetic Engine                      │
└───────────────────────────┬────────────────────────────┘
                            │ HTTP & WebSockets
┌───────────────────────────▼────────────────────────────┐
│               FastAPI + Socket.IO Backend              │
│  - QueueManager (Concurrent Worker Pool)               │
│  - Turbo Uploader (Multi-Connection MTProto Engine)    │
│  - Zero-Copy Direct Range Byte Slicer                  │
│  - SQLite WAL Database & In-Memory Dialog Cache        │
└───────────────────────────┬────────────────────────────┘
                            │ Direct MTProto (Encrypted)
┌───────────────────────────▼────────────────────────────┐
│              Official Telegram Servers                 │
└────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- Telegram API Credentials (`API_ID` & `API_HASH`) from [my.telegram.org](https://my.telegram.org)
- FFmpeg (optional, for automatic video thumbnail generation)

### 2. Installation

Clone the repository and create a virtual environment:
```bash
git clone <repository_url> TG_WEB
cd TG_WEB
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

### 3. Telegram Authentication

Configure your `.env` file in `backend/`:
```bash
cp backend/.env.example backend/.env
# Edit backend/.env and provide your API_ID and API_HASH
```

Authenticate your Telegram account:
```bash
python backend/setup_auth.py
```
*Follow the interactive prompt in the terminal to enter your phone number and login code.*

### 4. Run the Application

Start the high-speed backend server:
```bash
source venv/bin/activate
python backend/run.py
```

Open your browser and navigate to:
👉 **[http://localhost:8088](http://localhost:8088)**

---

## 🧪 Automated Testing

Run the full automated test suite:
```bash
source venv/bin/activate
pytest backend/tests -v
```

```
============================== test session starts ==============================
tests/test_all.py::test_file_detector PASSED                             [  9%]
tests/test_all.py::test_file_splitter PASSED                             [ 18%]
tests/test_all.py::test_queue_manager_lifecycle PASSED                   [ 27%]
tests/test_all.py::test_database_history PASSED                          [ 36%]
tests/test_all.py::test_api_endpoints PASSED                             [ 45%]
tests/test_all.py::test_string_chat_id_history PASSED                    [ 54%]
tests/test_all.py::test_socket_item_serialization PASSED                 [ 63%]
tests/test_all.py::test_fast_uploader_part_logic PASSED                  [ 72%]
tests/test_all.py::test_thumbnail_fallback PASSED                        [ 81%]
tests/test_all.py::test_zero_copy_chunk_config PASSED                    [ 90%]
tests/test_all.py::test_concurrent_queue_manager PASSED                  [100%]
============================== 11 passed in 5.79s ==============================
```

---

## 📡 API Overview

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/upload` | Stream file to server and enqueue for MTProto upload |
| `GET` | `/api/tasks` | Fetch list of active and queued tasks |
| `POST` | `/api/tasks/{id}/pause` | Pause an active upload task |
| `POST` | `/api/tasks/{id}/resume` | Resume a paused upload task |
| `POST` | `/api/tasks/{id}/cancel` | Cancel an upload task |
| `GET` | `/api/chats` | Fetch user dialogs (Saved Messages, channels, groups) |
| `GET` | `/api/history` | Fetch upload activity history |
| `DELETE`| `/api/history/clear` | Clear upload history records |
| `GET` | `/api/auth/status` | Get Telegram MTProto connection status |

---

## 📄 License

This project is licensed under the MIT License.
