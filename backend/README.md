# ClipKar Backend

FastAPI service that turns one long Hindi/Hinglish video into 5 vertical
short clips with burned-in animated subtitles.

## Setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your OPENAI_API_KEY and ANTHROPIC_API_KEY
python main.py
```

The server listens on `http://localhost:8000`.

## Endpoints

| Method | Path                          | Description                                   |
| ------ | ----------------------------- | --------------------------------------------- |
| GET    | `/health`                     | Sanity check                                  |
| POST   | `/videos/upload`              | Multipart upload (`file` field)               |
| GET    | `/videos`                     | List all videos, newest first                 |
| GET    | `/videos/{id}`                | Single video + nested clips                   |
| DELETE | `/videos/{id}`                | Delete video, clips, and on-disk artifacts    |
| GET    | `/clips/{id}/preview`         | Inline MP4 stream for `<video>` players       |
| GET    | `/clips/{id}/download`        | Forces download with `Content-Disposition`    |

## Storage layout

```
backend/storage/
├── uploads/    # original uploads ({video_id}_{filename})
├── clips/      # rendered shorts ({video_id}_clip_{1..5}.mp4)
├── temp/       # extracted audio + transient .ass subtitle files
└── clipkar.db  # SQLite database
```

## Pipeline (services/pipeline.py)

1. Extract mono 16 kHz MP3 audio with FFmpeg.
2. Transcribe via OpenAI Whisper with word + segment timestamps.
3. Ask Claude Haiku to pick the 5 best 30-60 second moments and write hooks.
4. For each pick: build an ASS subtitle file (hook overlay + word-by-word
   karaoke-style highlight) and burn it in via FFmpeg, cropping to 9:16.
5. Save Clip rows in SQLite. On crash, the next run will skip clips that
   already exist and resume from the next sequence number.

## Tuning

- Edit `services/claude_analyze.py` `SYSTEM_PROMPT` to bias selection toward
  your niche (finance, comedy, motivational, etc.).
- Edit `services/renderer.py` constants (`PRIMARY_FONT`, `WORDS_PER_LINE`,
  `HOOK_DURATION_SEC`) to change the look of the burned-in captions.
