# Story Shorts

Status: draft for review, 2026-11-19.

## Why

The channel analytics (`docs/reports/2026-11-19-channel-analytics.md`) show the channel's views come from
Shorts about one specific, named legend or mystery, under a minute long, with the place in the title.
*SS Baychimo*, *Chir-Batti* and *Robert the Doll* account for over 120k views between them. Broad
conspiracy theories got a median of 6 views. This spec adds that format to the pipeline as the main daily
video, with two guarantees viewers can rely on: the facts are right and the names are said correctly.

## What a story Short is

- **Topic.** One named, real legend, mystery or specific incident, tied to a place and, where known, a date.
  Examples: a ghost ship, a set of mystery lights, a cursed object, a cryptid sighting, a haunted building,
  or one specific conspiracy claim such as the Pentagon plane-or-missile question.
- **Length.** 35-55 s of narration, about 90-130 words.
- **Structure.** A hook line in the first two seconds, then the legend as people tell it, then what the
  record actually shows, then an eerie closing line. Theories are always framed as claims ("the story
  goes...", "believers point to...", "the records show..."), never stated as fact.
- **Pictures.** 4-6 images, one for each beat of the story, using the same style packs, motion and grading
  as the quote videos.
- **Sound.** One continuous narration over generated music, with the existing loudness levelling and
  ducking.
- **Captions.** The narration is shown phrase by phrase, with the existing karaoke word highlighting.
- **Title.** "Name: what it is, where" (for example "The Ghost Lights of Hessdalen: Norway's Unsolved
  Glow"). No question titles and no hashtags in the title. The description carries the hashtags plus a
  "Sources" list.

## Pipeline

```
pick topic -> research (web search) -> write script -> check every claim
  -> pronunciation guide -> narrate -> listen back -> images -> render -> upload
```

### 1. Pick the topic

- The model proposes 8 candidates, guided by a weighted category list drawn from the analytics: cursed
  objects, mystery lights, ships and the sea, cryptids, haunted places, and specific incidents.
  Categories are re-weighted from the weekly analytics pull once there is enough data, and every category
  keeps a minimum share for exploration.
- Rejected candidates:
  - anything in a new `story_history.jsonl` (never repeat a topic)
  - anything the channel has already covered (matched against the titles of past uploads)
  - anything that fails the content rules below
- One candidate is picked at random, weighted by category.

### 2. Research

- One web-search call collects a research brief:
  - names with their pronunciations
  - places, dates and key events
  - who claimed what, and what documents, investigations or experts say
  - 3-6 source URLs
- If fewer than 2 independent sources are found, the topic is dropped and the next candidate is tried.

### 3. Write

- A second call writes the script **only from the brief**, split into 4-6 beats. Each beat has:
  - narration text
  - an image scene (the same rules as quote scenes: no real faces, logos, gore, text or borders)
  - an ElevenLabs delivery tag
- It also returns a hook score and the title.

### 4. Check every claim

The quote verifier's approach, made stricter:

- A separate call pulls every factual claim out of the script (names, dates, places, numbers, events,
  who said what) and confirms each one against the sources by web search.
- Each claim comes back as supported, unsupported or wrong, with a correction.
- If anything is wrong or unsupported, the script is rewritten once with those corrections, then checked
  again. If it fails the second check, the topic is dropped.
- The verified claim list and sources are saved with the run, so any upload can be audited later.

### 5. Pronunciation

- The research brief lists every proper noun with a respelling (for example "Chir-Batti: cheer BAH-tee")
  and, where the sources give one, IPA.
- Names are kept in an ElevenLabs pronunciation dictionary owned by the project, so a name is said the
  same way in every video. The dictionary grows over time.
  - "Alias" rules (replace a word with a sound-it-out spelling) are the safe default.
  - Whether `eleven_v3` honours IPA "phoneme" rules needs to be confirmed during build. If it doesn't,
    aliases are used throughout.

### 6. Listen back

- Each narration clip is transcribed with the timestamped Whisper transcription already used in testing.
  The proper nouns in the transcript are compared with the script using a sound-alike match, not exact
  spelling, because a speech-to-text model also stumbles on rare names.
- A clip with a mismatched name is regenerated with the respelling inline, up to 2 times.
- If it still fails, the video is not uploaded. The run is logged as failed and the daily slot falls back
  to a quote Short, so the channel never publishes a mispronounced name it has detected.

### 7. Render and upload

- The video reuses the existing clip renderer (motion, ripple, flicker, grade, transitions) and the
  narration and music mixer.
- New: a single narration track is cut into beats using the ElevenLabs word timings, and each beat gets
  its own image and captions.
- Upload metadata:
  - the new title style
  - a description with sources
  - `containsSyntheticMedia` set to true
  - the catalog records `episode_format: story` and the topic, so analytics can compare stories with
    quote Shorts

## Content rules

- Real legends, documented mysteries and specific historical incidents only. Nothing invented and
  presented as real.
- No accusing named, living private people of crimes or misconduct. Public figures only where the sources
  support it.
- Leave out conspiracy topics that YouTube treats as harmful misinformation:
  - elections
  - vaccines and medical claims
  - ongoing disasters or tragedies
  - theories that target an ethnic, religious or other protected group
- Treat recent deaths respectfully: no topics where victims' families are likely still grieving (roughly
  the last 25 years) unless the story is already widely documented and the script stays factual.
- The same image rules as quote videos: no gore, corpses, hanged figures or real people's faces.

## Schedule

- The daily slot alternates between formats by weight. The default is story Shorts 5 of 7 days and quote
  Shorts 2 of 7, recorded in `episode_history.jsonl`.
- Weights move with the analytics once both formats have about 20 videos with metrics.

## Cost per story Short (estimate)

| Step | Estimate |
|---|---|
| Topic, research, script and claim check (about 4 web-search calls) | ~$0.10-0.25 |
| Narration, about 120 words plus retries | ~2-4k ElevenLabs credits |
| Listen-back transcription | <$0.01 |
| Images and music | local, $0 |

## Build order

1. Story history, topic picker and content-rule filter. Unit tests use a fake client, like the quote tests.
2. Research, script writer and claim checker, with saved claim lists.
3. Pronunciation dictionary and listen-back check.
4. Beat-based renderer and metadata.
5. Format rotation in the episode planner.
6. Three dry runs on lola. The user reviews them before the first public upload.

## Open questions for the user

1. Is the 5:2 story-to-quote split right, or should quote Shorts become an occasional special?
2. Should the channel remake its biggest hits as new videos (for example a deeper Baychimo story), or
   always find new topics?
3. Should there be a meditation variant: a slow, whispered story of one place, still under a minute?
