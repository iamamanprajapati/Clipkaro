# ClipKar

**Local-only AI shorts generator for Indian creators.**

ClipKar takes one long Hindi / Hinglish / English video (podcast,
interview, monologue) and produces **5 vertical short clips** (Instagram
Reels / YouTube Shorts, 9:16) with **burned-in animated word-by-word
subtitles** and **punchy 5-8 word hook titles** picked by an LLM.

Everything runs on your laptop. The only external calls are to OpenAI
Whisper (transcription) and Anthropic Claude Haiku (clip selection).
There is no signup, no cloud storage, no payment, no deployment.

---

## Prerequisites

You need these installed locally before running ClipKar:

### 1. Python 3.11 or higher

```bash
# macOS (recommended)
brew install python@3.11

# Ubuntu / Debian
sudo apt install python3.11 python3.11-venv

# Windows
# Download installer from https://www.python.org/downloads/
```

### 2. Node.js 20 or higher

```bash
# macOS
brew install node

# Ubuntu / Debian
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install nodejs

# Windows
# Download installer from https://nodejs.org/
```

### 3. FFmpeg (with `ffmpeg` and `ffprobe` on PATH)

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Windows
# Download from https://www.gyan.dev/ffmpeg/builds/ and add the bin/
# folder to your PATH.
```

Verify with `ffmpeg -version`.

### 4. Noto Sans Devanagari font (for Hindi subtitles)

ClipKar burns subtitles using the system font "Noto Sans Devanagari" by
default. Without it, Devanagari characters will render as empty boxes.

```bash
# macOS
brew install --cask font-noto-sans-devanagari

# Ubuntu / Debian
sudo apt install fonts-noto

# Windows
# Download from https://fonts.google.com/noto/specimen/Noto+Sans+Devanagari
# and right-click the .ttf files → Install for all users.
```

### 5. API keys

- **OpenAI API key** with credit (used for `whisper-1`):
  https://platform.openai.com/api-keys
- **Anthropic API key** with credit (used for `claude-haiku-4-5-20251001`):
  https://console.anthropic.com/

---

## Quick start (5 steps)

### 1. Open this folder in your editor

```bash
cd clipkar
```

### 2. Backend

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Open .env and paste your OPENAI_API_KEY and ANTHROPIC_API_KEY
python main.py
```

Backend now serves on `http://localhost:8000`. Leave this terminal open.

### 3. Frontend (in a second terminal)

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

Frontend now serves on `http://localhost:3000`.

### 4. Open the app

Visit **http://localhost:3000**.

### 5. Upload a video and wait

Click **New upload**, drop a video, click **Upload**. You will be
redirected to a status page that polls the backend every 3 seconds. After
3-5 minutes you’ll see 5 clips with inline players and download buttons.

---

## How to get a test video

```bash
# Install yt-dlp once
brew install yt-dlp        # or: pip install -U yt-dlp

# Download a Hindi YouTube interview (medium-quality is enough)
yt-dlp -f "best[height<=720]" -o "test.mp4" "https://www.youtube.com/watch?v=XXXXXXXXXXX"
```

Then upload `test.mp4` through the ClipKar UI.

---

## Cost per video

Approximate API cost for a 20-minute video (as of late 2025):

| Service              | Cost                                  |
| -------------------- | ------------------------------------- |
| OpenAI Whisper       | ~$0.12 (≈ ₹10) — $0.006/minute        |
| Anthropic Haiku      | ~$0.05 (≈ ₹4) — small prompt + JSON   |
| **Total per video**  | **≈ ₹15-25** depending on length      |

There are no per-clip storage fees because everything is local.

---

## Tuning the clip selection

The picking logic lives entirely in the prompt. To bias clips toward your
content niche, edit `backend/services/claude_analyze.py`:

- The `SYSTEM_PROMPT` constant controls **what kinds of moments** Claude
  picks (currently: strong opinions, surprising facts, emotional stories,
  aha insights, actionable advice, funny lines).
- The hook style rules at the bottom of the prompt control **how the
  hook titles are written** — language mix, length, tone.

Restart `python main.py` after editing.

You can also tune the burned-in caption look in
`backend/services/renderer.py`:

- `PRIMARY_FONT` — change the subtitle font.
- `WORDS_PER_LINE` — words shown per subtitle line (default 4).
- `HOOK_DURATION_SEC` — how long the hook title stays on screen.
- The styles in `_ass_header()` control colors, sizes, outline, and
  position of both overlays.

---

## Troubleshooting

**`ffmpeg not found`**
Install FFmpeg using the commands in the Prerequisites section. Verify
with `ffmpeg -version`. Restart the backend.

**`Your ffmpeg build is missing libass (no subtitles filter)`**
Some Homebrew bottles of `ffmpeg` (notably the v8.x bottle on Apple
Silicon) ship without `libass`, so subtitle burning is impossible.
Two fixes:

- Install the full build: `brew install ffmpeg-full` or
  `brew install ffmpeg@7` (which includes libass).
- Or drop a static libass-enabled build into `backend/bin/ffmpeg` and
  `backend/bin/ffprobe`. The renderer prefers binaries in `backend/bin/`
  over the system `PATH`, so this works without uninstalling anything.
  Easiest source for macOS: https://evermeet.cx/ffmpeg/ — download the
  `ffmpeg` and `ffprobe` zips, unzip into `backend/bin/`, then `chmod +x`
  both files.

**Subtitles appear as boxes (empty rectangles)**
Your system is missing a Devanagari font. Install Noto Sans Devanagari
(see Prerequisites step 4) and re-run a video. You can also change
`PRIMARY_FONT` in `backend/services/renderer.py` to whichever font you
have installed.

**`OPENAI_API_KEY is not configured`**
Make sure `backend/.env` exists (copied from `.env.example`) and contains
real keys. Restart the backend.

**Rate limit / quota errors from OpenAI or Anthropic**
Add credit to your API account and try again. Rate-limited videos end up
in the **failed** state with the provider error message — delete and
re-upload to retry.

**Slow processing**
This is expected. A 20-minute video typically takes **3-5 minutes** to
process end-to-end on a modern laptop. Most of the time is FFmpeg
encoding the 5 final MP4s. Watch the terminal — every step is logged.

**Backend crashes mid-render and restarts**
The pipeline is retry-safe. If clips 1-3 are already in the database when
you re-run processing for the same video, ClipKar will skip them and
resume from clip 4. To do this manually, hit the backend with the same
upload (the row’s status will be 'failed'; see "What's next" if you want
a UI button for retries — it’s not built yet).

**`Network error — is the backend running?`**
The frontend at `localhost:3000` could not reach the backend at
`localhost:8000`. Check that `python main.py` is still running in the
other terminal.

---

## What's next (improvements to add later)

- **Face-centered crop** using OpenCV / MediaPipe so the speaker stays
  centered when you crop a wide 16:9 video to 9:16.
- **Custom subtitle styles** (multiple presets: Reels-style yellow,
  caption-style box, TikTok-style bouncy text).
- **English / Tamil / Telugu / Marathi support** — Whisper already
  handles them; you’d just want different fonts and possibly a
  language-aware Claude prompt.
- **Background music** — auto-pick royalty-free tracks and mix at -18 dB
  under the original audio.
- **Retry button** in the UI to re-trigger processing for a failed video.
- **Multi-user / cloud version** — would require adding auth (Clerk or
  NextAuth), object storage (R2/S3), and a real queue (Celery + Redis).
  Out of scope for this local build.
