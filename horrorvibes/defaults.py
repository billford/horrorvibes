"""Built-in configuration defaults.

Every setting has a value here, so a minimal or even empty config.yaml is valid. ``load_config`` in
horrorvibes/config.py deep-merges config.yaml over this and validates the result.
"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "run": {
        "quote_count": 9,
        "duration_per_quote_sec": 10,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "quotes_dir": "./quotes",
        "images_dir": "./images",
        "frames_dir": "./frames",
        "output_dir": "./output",
        "quotes_history_path": "./quotes_history.txt",
        # Optional: also copy each completed video here (e.g. an external
        # drive). None disables archiving -- videos stay in output_dir only.
        "completed_video_dir": None,
        # Durable, append-only record of every quote that's appeared in a
        # generated video, so you can search back later for which video a
        # particular quote/movie was used in.
        "video_catalog_path": "./video_catalog.jsonl",
    },
    "quotes": {
        # Responses API model. Reasoning models reject temperature, so it is
        # only sent when set (null = omit). See docs/specs/v3-diversity.md.
        "model": "gpt-5.6-sol",
        "reasoning_effort": "low",
        "temperature": None,
        "max_attempts": 4,
        # Second pass that checks each candidate with the web_search tool and
        # drops lines it can't confirm. A failed check keeps the candidates.
        "verify": True,
        "verify_model": None,  # null = same as model
        # Quote text similarity (0-1) at or above which a quote counts as a
        # repeat of one already used.
        "fuzzy_dup_threshold": 0.88,
        # Max recently-used titles/quotes listed in the prompt as exclusions.
        "exclusion_prompt_limit": 150,
        # Each quote gets its own (decade, subgenre, region, medium) brief so a
        # batch spreads across the genre instead of the same famous films.
        "themes": [
            "slasher", "supernatural possession", "haunted house", "ghost story",
            "folk horror", "body horror", "cosmic horror", "psychological horror",
            "creature feature", "zombie", "vampire", "werewolf", "witchcraft",
            "found footage", "giallo", "gothic horror", "sci-fi horror",
            "survival horror", "home invasion", "horror comedy", "cult horror",
            "killer doll or toy", "anthology", "monster movie",
        ],
        "decades": [1920, 1930, 1940, 1950, 1960, 1970, 1980, 1990, 2000, 2010, 2020],
        # Relative sampling weights. Weighted toward English-language titles,
        # where quote recall (and verification) is most reliable.
        "regions": {
            "American": 5, "British": 2, "Japanese": 1, "South Korean": 1,
            "Spanish": 0.5, "Italian": 0.5, "French": 0.5, "Mexican": 0.5,
            "Canadian": 0.5, "Australian": 0.5,
        },
        # Fraction of slots that ask for a TV series instead of a film.
        "tv_share": 0.3,
        # Durable record of which movie/show each quote came from, so the
        # same one isn't quoted again too soon.
        "movie_history_path": "./movie_history.jsonl",
        "movie_cooldown_days": 30,
    },
    "image": {
        # Start of the fallback chain mflux -> local_sd -> openai -> gradient.
        "backend": "mflux",
        # FLUX.2 Klein through mflux (Apple MLX) -- diffusers' MPS path is
        # unreliable for FLUX. Generated at a 9:16 size divisible by 16 and
        # upscaled by the compositor/motion step.
        "mflux_command": "mflux-generate-flux2",
        "mflux_model": "flux2-klein-4b",
        "mflux_steps": 4,
        "mflux_width": 768,
        "mflux_height": 1344,
        "mflux_timeout_sec": 900,
        # Full SDXL base, not sdxl-turbo: turbo's 1-4 step distillation trades
        # away prompt adherence for speed, and images stopped meaningfully
        # relating to their quote/movie as a result. Slower per image, but
        # runs are once a day, so there's no real-time pressure.
        "sd_model": "stabilityai/stable-diffusion-xl-base-1.0",
        "sd_device": "mps",
        "sd_steps": 30,
        "sd_guidance_scale": 7.0,
        "sd_timeout_sec": 300,
        "openai_model": "gpt-image-2",
        "openai_timeout_sec": 60,
        "style_suffix": (
            "expressionist horror painting, dramatic chiaroscuro lighting, "
            "high-contrast brushwork, bruised muted palette, textured canvas"
        ),
    },
    "music": {
        "backend": "elevenlabs",
        "model_id": "music_v1",
        "mood_anchor": "meditative horror ambient, no vocals, no percussion hits",
        "mood_pool": ["slow", "sparse", "drone", "unsettling calm", "minor key", "distant"],
        "mood_pool_sample_size": 3,
        "api_timeout_sec": 120,
        "max_retries": 3,
        "retry_backoff_sec": 5,
        "fallback_dir": "./audio",
        # Curated library tracks are almost always registered with YouTube
        # Content ID, so a video using one gets auto-claimed even when you hold
        # a valid license. By default, a run that fell back to a curated track
        # keeps the video locally and skips the upload instead of publishing it.
        "allow_curated_upload": False,
        "local_model": "stabilityai/stable-audio-open-1.0",
        "local_device": "mps",
        "local_steps": 100,
        "local_segment_sec": 40,
        "local_crossfade_sec": 3,
        "local_negative_prompt": "vocals, singing, lyrics, percussion, drums, low quality",
    },
    "compositor": {
        "font_paths": [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ],
        "quote_font_size": 60,
        "movie_font_size": 48,
        "max_chars_per_line": 25,
        "scrim_opacity": 150,
    },
    "publish": {
        "youtube_upload": True,
        "privacy_status": "private",
        "category_id": "17",
        "client_secrets_path": "./client_secret.json",
        "token_path": "./token.json",  # nosec B105 - a file path default, not a credential value
        # YouTube's altered/synthetic media disclosure: images, music and narration are AI-generated.
        "contains_synthetic_media": True,
        # Shown in every description so viewers can see (and reuse) how the video was made. null = omit.
        "source_url": "https://github.com/billford/horrorvibes",
    },
    "voiceover": {
        "enabled": False,
        "voice_id": "2EiwWnXFnvU5JabPnv8n",  # "Clyde" -- gruff, unsettling; audition and swap freely
        # eleven_v3 honours per-quote delivery tags like [whispers]; if a v3
        # request fails, the clip is retried once on fallback_model_id
        # (untagged) and then on the default voice.
        "model_id": "eleven_v3",
        "fallback_model_id": "eleven_multilingual_v2",
        "use_delivery_tags": True,
        # Character timings for word-synced captions (/with-timestamps). If
        # the model returns none, word times are estimated from clip length.
        "use_timestamps": True,
        "stability": 0.5,
        "similarity_boost": 0.75,
        "style": 0.3,
        "use_speaker_boost": True,
        "speed": 0.75,  # slower, more menacing delivery -- 0.7 is ElevenLabs' floor before quality degrades
        "api_timeout_sec": 60,
        "max_retries": 3,
        "retry_backoff_sec": 5,
        # Music volume during each narration's exact window (linear, 0-1) and
        # narration's own gain boost -- see voiceover.py for why this replaced
        # a sidechaincompress-based approach.
        "duck_volume": 0.3,
        "narration_gain": 1.0,
        # Every narration clip is levelled to narration_lufs and the music bed
        # to music_lufs before ducking, so a whispered line is as audible as a
        # shouted one and a loud music render can't bury the voice.
        "narration_lufs": -16.0,
        "music_lufs": -27.0,
        # Extra seconds a quote stays on screen past its narration's own
        # length, so the next quote's narration never starts before this
        # one has actually finished.
        "narration_pad_sec": 1.0,
    },
    "motion": {
        # Ken Burns zoom/pan per quote, colour grade + film texture from the
        # style pack, and xfade transitions. Any failure falls back to the
        # static frame-by-frame assembly, so a run never dies here.
        "enabled": True,
        "transition_sec": 0.8,
        "zoom_amount": 0.18,
        # Keep the image alive under the captions: slow pan (fraction of the
        # zoom slack), heat-haze ripple (pixels) and candle flicker
        # (brightness). 0 turns each off.
        "drift": 0.8,
        "ripple_px": 4.5,
        "flicker": 0.025,
        # Backgrounds are upscaled this much before zoompan to avoid jitter.
        "upscale_factor": 1.5,
        # karaoke: words light up as they're narrated. static: whole quote.
        "caption_mode": "karaoke",
        "crf": 20,
        "preset": "medium",
    },
    "episodes": {
        "enabled": True,
        "history_path": "./episode_history.jsonl",
        # Chance a run is a themed episode rather than a mixed bag.
        "themed_share": 0.4,
        "quote_count_min": 4,
        "quote_count_max": 5,
        # {n} = number of quotes. Mixed episodes pick one at random.
        "mixed_headlines": [
            "{n} Bone-Chilling Horror Quotes",
            "{n} Horror Lines That Still Haunt Us",
            "{n} Quotes Horror Fans Never Forgot",
            "Whispers From the Dark: {n} Horror Quotes",
            "{n} Lines You Shouldn't Hear Alone",
        ],
        # Each format may narrow the quote briefs (themes / decades /
        # regions / tv_share) and add an instruction for the model.
        "formats": [
            {"name": "folk_horror", "headline": "Folk Horror Night: {n} Quotes",
             "themes": ["folk horror", "witchcraft", "cult horror"]},
            {"name": "villains", "headline": "{n} Lines Spoken by Horror Villains",
             "instruction": "Every line must be spoken by the film's villain, monster or killer."},
            {"name": "last_words", "headline": "{n} Chilling Last Words in Horror",
             "instruction": "Prefer final lines, closing narration, or a character's last words."},
            {"name": "asian_horror", "headline": "{n} Quotes From Asian Horror",
             "regions": {"Japanese": 1, "South Korean": 1, "Thai": 0.5, "Chinese": 0.5, "Indonesian": 0.5}},
            {"name": "eighties", "headline": "{n} Quotes From 80s Horror", "decades": [1980]},
            {"name": "golden_age", "headline": "{n} Quotes From Classic Monster Movies",
             "decades": [1920, 1930, 1940, 1950, 1960],
             "themes": ["monster movie", "gothic horror", "vampire", "werewolf", "creature feature"]},
            {"name": "haunted_tv", "headline": "{n} Quotes From Horror TV", "tv_share": 1.0},
            {"name": "cosmic", "headline": "{n} Quotes From Beyond: Cosmic & Sci-Fi Horror",
             "themes": ["cosmic horror", "sci-fi horror", "body horror"]},
        ],
    },
    # Story Shorts: one researched, fact-checked legend per video (docs/specs/story-shorts.md).
    "stories": {
        "enabled": False,
        "model": None,  # null = quotes.model
        "reasoning_effort": "medium",
        "history_path": "./story_history.jsonl",
        "audit_dir": "./stories",
        # Saved upload titles (channel_analytics.json) the topic picker avoids repeating.
        "channel_snapshot_path": "./channel_analytics.json",
        # Relative weights for the topic category. Ships lead: the channel's most reliable topic.
        "categories": {
            "ghost ships, warships, shipwrecks and mysteries of the sea": 3.0,
            "cursed or haunted objects": 1.5,
            "unexplained lights in the sky or landscape": 1.5,
            "cryptids and lake monsters": 1.2,
            "haunted places and local legends": 1.0,
            "one specific, documented incident at the heart of a conspiracy theory": 1.0,
        },
        "topic_candidates": 8,
        "max_topic_attempts": 3,
        "min_sources": 2,
        # About 2.1 narrated words a second plus pauses between beats: 112 words came out at 60 s.
        "words_min": 80,
        "words_max": 105,
        "beats_min": 4,
        "beats_max": 6,
        # Longest beat: one beat's narration is one screen of captions.
        "beat_words_max": 32,
        # Weekdays (lola local time) that run a story instead of a quote Short.
        "days": ["mon", "thu", "fri", "sat", "sun"],
        # Silence after each beat's narration before the next image.
        "beat_pad_sec": 0.5,
        # Narration voice for stories (the user's own clone, once made). null = voiceover.voice_id.
        "voice_id": None,
    },
    # Visual + sonic identity, rotated least-recently-used per run. Fonts
    # missing on the machine fall through to compositor.font_paths.
    "style_packs": [
        {
            "name": "expressionist_oil",
            "image_style": "expressionist horror oil painting, dramatic chiaroscuro lighting, "
                           "high-contrast brushwork, bruised muted palette, textured canvas",
            "font_paths": ["/System/Library/Fonts/Supplemental/Georgia Bold.ttf"],
            "layout": "center", "text_color": [245, 238, 225], "highlight_color": [255, 255, 255],
            "scrim_opacity": 120, "stroke_width": 0,
            "grade": "curves=preset=strong_contrast,eq=saturation=0.8",
            "overlay": "grain", "vignette": True,
            "transitions": ["fade", "dissolve", "fadeblack"],
            "music_mood_anchor": "meditative horror ambient, no vocals, no percussion hits",
            "music_mood_pool": ["slow", "sparse", "drone", "unsettling calm", "minor key", "distant"],
            "voice_id": None,
        },
        {
            "name": "grindhouse_poster",
            "image_style": "1970s grindhouse exploitation-film painted artwork style, lurid saturated "
                           "colours, airbrushed illustration, halftone texture, dramatic composition",
            "font_paths": ["/System/Library/Fonts/Supplemental/Impact.ttf"],
            "layout": "upper", "text_color": [240, 220, 170], "highlight_color": [255, 60, 40],
            "scrim_opacity": 0, "stroke_width": 4,
            "grade": "curves=preset=vintage,eq=contrast=1.12:saturation=1.25",
            "overlay": "grain", "vignette": True,
            "transitions": ["wipeleft", "slideup", "fadeblack"],
            "music_mood_anchor": "1970s exploitation horror score, analog synthesizer, no vocals",
            "music_mood_pool": ["pulsing", "detuned", "menacing", "arpeggiated", "low drone", "tense"],
            "voice_id": "nPczCjzI2devNBz1zQrb",
        },
        {
            "name": "vhs_found_footage",
            "image_style": "still frame from degraded 1990s camcorder found footage, night vision, "
                           "motion blur, harsh on-camera light, grainy low resolution",
            "font_paths": ["/System/Library/Fonts/Supplemental/Courier New Bold.ttf"],
            "layout": "lower", "text_color": [210, 210, 210], "highlight_color": [255, 255, 255],
            "scrim_opacity": 140, "stroke_width": 0,
            "grade": "eq=saturation=0.55:contrast=1.1,colorbalance=gs=0.06:gm=0.04",
            "overlay": "vhs", "vignette": False,
            "transitions": ["pixelize", "hblur", "fadeblack"],
            "music_mood_anchor": "lo-fi tape hiss horror ambience, warped cassette, no vocals",
            "music_mood_pool": ["wavering", "detuned", "static", "distant hum", "unsettling", "sparse"],
            "voice_id": None,
        },
        {
            "name": "woodcut_folk",
            "image_style": "black and white woodcut print style, rough carved lines, "
                           "stark high-contrast composition",
            "font_paths": ["/System/Library/Fonts/Supplemental/Luminari.ttf"],
            "layout": "center", "text_color": [235, 225, 200], "highlight_color": [255, 250, 235],
            "scrim_opacity": 150, "stroke_width": 0,
            "grade": "curves=preset=darker,eq=saturation=0.4",
            "overlay": "grain", "vignette": True,
            "transitions": ["fade", "circleclose", "dissolve"],
            "music_mood_anchor": "folk horror drone, droning strings, eerie hum, no vocals",
            "music_mood_pool": ["ancient", "pastoral dread", "slow", "modal", "hollow", "ritual"],
            "voice_id": "JBFqnCBsd6RMkjVDRZzb",
        },
        {
            "name": "cosmic_void",
            "image_style": "cosmic horror digital painting, eerie bioluminescent teal and violet light, "
                           "dreamlike surreal atmosphere",
            "font_paths": ["/System/Library/Fonts/Avenir Next Condensed.ttc",
                           "/System/Library/Fonts/Supplemental/Futura.ttc"],
            "layout": "center", "text_color": [190, 225, 235], "highlight_color": [240, 255, 255],
            "scrim_opacity": 90, "stroke_width": 0,
            "grade": "eq=saturation=1.1:contrast=1.05,colorbalance=bs=0.12:rm=0.04:bm=0.06",
            "overlay": "none", "vignette": True,
            "transitions": ["circleopen", "radial", "fadewhite", "fade"],
            "music_mood_anchor": "vast cosmic horror drone, deep sub bass, no vocals, no percussion",
            "music_mood_pool": ["shimmering", "dissonant", "immense", "slow", "alien", "glacial"],
            "voice_id": None,
        },
        {
            "name": "polaroid_evidence",
            "image_style": "1970s amateur flash photography look, harsh on-camera flash, faded "
                           "washed-out colours, light leaks, film grain",
            "font_paths": ["/System/Library/Fonts/Supplemental/AmericanTypewriter.ttc"],
            "layout": "lower", "text_color": [235, 235, 225], "highlight_color": [255, 255, 255],
            "scrim_opacity": 160, "stroke_width": 0,
            "grade": "curves=preset=cross_process,eq=saturation=0.75",
            "overlay": "grain", "vignette": False,
            "transitions": ["slideleft", "fadeblack", "wiperight"],
            "music_mood_anchor": "unsettling minimal horror ambient, music box, no vocals",
            "music_mood_pool": ["fragile", "slow", "detuned", "sparse", "melancholy", "cold"],
            "voice_id": "pFZP5JQG7iQjIQuC4Bku",
        },
    ],
    "logging": {
        "level": "INFO",
        "file": "./logs/horrorvibes.log",
        "max_bytes": 5_000_000,
        "backup_count": 3,
    },
    "automation": {
        "min_hours_between_runs": 12,
        "state_file": "./logs/last_run.json",
    },
}
