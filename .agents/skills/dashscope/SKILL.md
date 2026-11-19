---
name: dashscope
description: DashScope (Alibaba Cloud Bailian / 阿里云百炼) integration — image generation (qwen-image-2.0-pro), Wan 3.0 video (wan3.0-video-prime), text-to-speech (qwen3-tts-flash), and ASR with word-level timestamps (qwen3-asr-flash-filetrans). Use when generating images via Qwen-Image, videos via Wan 3.0, narrating via Qwen-TTS, or transcribing with word-level timestamps via Qwen-ASR.
---

# DashScope

Requires `DASHSCOPE_API_KEY` in `.env`. Get one at https://dashscope.aliyun.com/.

## Current API

**CRITICAL:** DashScope's `/compatible-mode/v1/` only supports `/chat/completions` and `/embeddings`. Image generation, video generation, TTS, and ASR all use **DashScope-native endpoints** — not OpenAI-compatible paths.

All four tools use `Authorization: Bearer $DASHSCOPE_API_KEY`.

### Image Generation

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

- Model: `qwen-image-2.0-pro` (default), `qwen-image-2.1-pro`, `qwen-image-2.1-turbo`, `qwen-image-max`, `wan2.7-image`, `z-image-turbo`
- Body: `{model, input: {messages: [{role: "user", content: [{text: "prompt"}]}]}, parameters: {size: "W*H", n, prompt_extend, watermark}}`
- Image editing / I2I (`qwen-image-2.1-*`): put 1–10 `{image: url_or_data_uri}` objects before the text instruction in `content`; tool accepts `image_path` / `image_paths` / `image_url` / `image_urls`
- **Size format uses asterisk:** `"1024*1024"` not `"1024x1024"`
- Response: `output.choices[0].message.content[0].image` (URL, valid ~24h) — must download separately

### Video Generation (Wan 3.0)

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis
Header: X-DashScope-Async: enable
```

- Model: `wan3.0-video-prime` (default, faster), `wan3.0-video`
- Body: `{model, input: {prompt, media?}, parameters: {resolution, ratio, duration, audio, prompt_extend, watermark, seed}}`
- Text-to-video: only `prompt`, no `media`
- Image-to-video: `media: [{type: "first_frame", url}]`；可选再加 `{type: "last_frame", url}`
- Local first frames: pass `data:image/png;base64,...` in `media[].url`（工具 `image_path` 会自动编码）
- Poll: `GET /api/v1/tasks/{task_id}` → `output.video_url`
- Output: typically **30fps**; duration is **integer seconds** only (2–30)
- `audio: false` recommended when the project already has TTS / LatentSync
- `prompt_extend: false` recommended for locked shot prompts
- I2V: set `ratio: "adaptive"` so 480×832 first frames stay vertical (`15:26`)

### Text-to-Speech

```text
POST https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation
```

Same endpoint as image gen, different body.

- Model: `qwen3-tts-flash` (default), `qwen3-tts-instruct-flash`, `qwen-tts-2025-05-22`
- Body: `{model, input: {text, voice: "Cherry", language_type: "Auto"}}`
- Response: `output.audio.url` (WAV, valid ~24h) — must download separately

### ASR with Word-Level Timestamps

```text
POST https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription
Header: X-DashScope-Async: enable
```

- Model: `qwen3-asr-flash-filetrans` (NOT `qwen3-asr-flash` — the sync version has no word timestamps)
- Body: `{model, input: {file_url: "https://public-url/audio.mp3"}, parameters: {enable_words: true, language_hints: ["zh","en"]}}`
- Returns `task_id` → poll `GET /api/v1/tasks/{task_id}` until `SUCCEEDED` → download `output.result.transcription_url` → JSON with `transcripts[].sentences[].words[]`
- Timestamps in `begin_time`/`end_time` are in **milliseconds** — the tool normalizes to seconds

## OpenMontage Usage

### Image via selector

```python
from tools.graphics.image_selector import ImageSelector

result = ImageSelector().execute({
    "preferred_provider": "dashscope",
    "prompt": "一只猫坐在沙发上",
    "output_path": "projects/my-video/assets/images/cat.png",
})
```

### TTS via selector

```python
from tools.audio.tts_selector import TTSSelector

result = TTSSelector().execute({
    "preferred_provider": "dashscope",
    "text": "如果 AI 真的会改变未来，普通人到底该怎么参与？",
    "voice": "Cherry",
    "output_path": "projects/my-video/assets/audio/narration.wav",
})
```

### ASR directly (word timestamps for subtitles)

```python
from tools.analysis.dashscope_asr import DashscopeAsr

result = DashscopeAsr().execute({
    "audio_url": "https://example.com/narration.wav",
    "output_path": "projects/my-video/assets/audio/transcription.json",
})

# result.data["words"] is a flat list of {text, begin_time_seconds, end_time_seconds}
```

### Video (Wan 3.0 I2V from a locked first frame)

```python
from tools.video.dashscope_wan_video import DashscopeWanVideo

result = DashscopeWanVideo().execute({
    "operation": "image_to_video",
    "model": "wan3.0-video-prime",
    "prompt": "老太太仰头哭喊，镜头缓慢推近，白天自然光",
    "image_path": "projects/my-video/assets/images/S02.png",
    "resolution": "480P",
    "ratio": "adaptive",
    "duration": 4,
    "audio": False,
    "prompt_extend": False,
    "seed": 1102,
    "output_path": "projects/my-video/assets/video/S02.mp4",
})
```

## Recommended Workflow

1. **Image:** Generate a sample first. Check `prompt_extend: true` (default) — DashScope rewrites your prompt for better results. Disable if you need literal prompt adherence.
2. **Video:** For locked first frames, use `image_to_video` with `ratio=adaptive`, `audio=false`, `prompt_extend=false`. Duration is integer seconds only; trim in edit to match the scene plan.
3. **TTS:** Generate a 10-15 second sample before full narration. Approve voice and pacing before committing to full generation.
4. **ASR:** Audio must be at a **publicly accessible URL**. Upload to any public host (S3, etc.) first. Local paths are rejected with a clear error.
5. **Subtitles:** Build from `result.data["words"]` — each word has `begin_time_seconds` and `end_time_seconds`. Group words into caption phrases by language semantics, not fixed character count.

## Parameters

### Image (`dashscope_image`)
- `prompt` (required): text prompt
- `model`: default `qwen-image-2.0-pro`
- `size`: default `"1024*1024"` — **asterisk separator, not "x"**
- `n`: 1-6 images
- `negative_prompt`: things to avoid (max 500 chars)
- `prompt_extend`: default `true` — auto-rewrite prompt for better results
- `watermark`: default `false`
- `seed`: for reproducibility

### TTS (`dashscope_tts`)
- `text` (required): text to synthesize (max 600 chars for qwen3-tts-flash)
- `model`: default `qwen3-tts-flash`
- `voice`: default `"Cherry"` — other voices: `"Ethan"`, `"Chelsie"`, etc.
- `language_type`: default `"Auto"` — `"Chinese"`, `"English"`, `"Japanese"`, `"Korean"`
- `instructions`: natural language delivery instructions (only for `qwen3-tts-instruct-flash`)

### ASR (`dashscope_asr`)
- `audio_url` (required): **must be publicly accessible URL**
- `model`: `qwen3-asr-flash-filetrans` (only model that supports word timestamps)
- `language_hints`: default `["zh", "en"]`
- `enable_words`: default `true` — required for word-level timestamps
- `poll_interval_seconds`: default `5.0`
- `timeout_seconds`: default `300`

### Video (`dashscope_wan_video`)
- `prompt` (required)
- `operation`: `text_to_video` or `image_to_video`
- `model`: default `wan3.0-video-prime`
- `image_path` / `image_url`: first frame for I2V
- `resolution`: `480P` / `720P` / `1080P`
- `ratio`: default `adaptive` for I2V
- `duration`: integer seconds 2–30
- `audio`: default `false`
- `prompt_extend`: default `false`

## Troubleshooting

- **Image size error:** Use `"W*H"` with asterisk, not `"WxH"`. Example: `"2048*2048"`.
- **TTS no audio URL:** Check `output.audio.url` — if empty, the model name or voice may be wrong.
- **ASR "file not accessible":** `audio_url` must be publicly reachable. DashScope servers fetch the file; local paths and auth-gated URLs don't work.
- **ASR poll timeout:** Increase `timeout_seconds` (default 300). Long audio files take longer to transcribe.
- **ASR no word timestamps:** Ensure `enable_words: true` and model is `qwen3-asr-flash-filetrans` (not the sync `qwen3-asr-flash`).
- **Auth error (401):** Verify `DASHSCOPE_API_KEY` is set. Use `Authorization: Bearer $KEY` header.

## Safety

Never print or write the API key to logs, metadata, patches, or project artifacts. `.env.example` should contain only empty variable names. The tool's `_safe_error()` method redacts the key from error messages.
