# ElevenLabs v3 Reference

Use this reference when generating podcast dialogue audio with ElevenLabs.

## Defaults

- API key environment variable: `ELEVENLABS_API_KEY`
- Endpoint: `POST https://api.elevenlabs.io/v1/text-to-dialogue`
- Default model: `eleven_v3`
- Default output format: `mp3_44100_128`
- Default reliable chunk budget: 2,000 total text characters per dialogue request

## Voice Mapping

Each host needs a voice ID before live audio generation:

```bash
python scripts/generate_audio.py script.csv \
  --voice host_a=VOICE_ID_A \
  --voice host_b=VOICE_ID_B
```

Keep internal host labels stable (`host_a`, `host_b`) even when the user gives display names. Voice choices can change without rewriting the script.

For two-host first drafts, use the proven defaults unless the user supplied specific voices:

| host | default voice | voice ID |
| --- | --- | --- |
| `host_a` | Roger - Laid-Back, Casual, Resonant | `CwhRBWXzGAHq8TQ4Fs17` |
| `host_b` | Sarah - Mature, Reassuring, Confident | `EXAVITQu4vr4xnSDxMaL` |

Run:

```bash
python scripts/generate_audio.py script.csv --use-default-voices
```

## Audio Tags

Eleven v3 can respond to square-bracket performance tags. Use them sparingly and only when the user wants expressive delivery.

Useful examples:

```text
[laughs]
[whispers]
[sighs]
[curious]
[excited]
[sarcastic]
[clears throat]
```

Do not use SSML break tags for Eleven v3. Prefer punctuation, short turns, ellipses, or square-bracket performance tags.

## Pronunciation

Prefer ElevenLabs pronunciation dictionaries over inline text replacement. Dictionary rules are the right control surface for approved high-risk terms:

- Use `phoneme` rules with `alphabet=ipa` when a brand, drug, person, institution, or technical term needs exact pronunciation.
- Use `alias` rules when a term should expand or be spoken as a simpler phrase.
- Apply dictionaries during dialogue generation with `--pronunciation-locator DICTIONARY_ID:VERSION_ID`. ElevenLabs applies dictionary locators in order and supports up to three per request.
- Use `scripts/build_pronunciation_dictionary.py` to create or update the ElevenLabs dictionary from approved glossary rows, and export a reviewable `.pls` lexicon when helpful.

Glossary columns:

```text
term,type,source,pronunciation,pronunciation_kind,alphabet,language_code,status,notes
```

Use `pronunciation_kind=phoneme` and `alphabet=ipa` for IPA entries. Use `status=approved` or `status=tested` before exporting. Keep uncertain entries as `status=draft` or `pronunciation_kind=review`.

Example:

```csv
term,type,source,pronunciation,pronunciation_kind,alphabet,language_code,status,notes
JELMYTO,brand-or-product,brand_terms.md,dʒɛlˈmaɪtoʊ,phoneme,ipa,en-US,approved,Confirmed for ElevenLabs smoke test.
FDA,acronym,source.md,eff dee ay,alias,,en-US,approved,Alias avoids accidental word pronunciation.
```

Create a dictionary and review artifact:

```bash
python scripts/build_pronunciation_dictionary.py pronunciation_glossary.csv \
  --create \
  --out-manifest pronunciation_dictionary_manifest.json \
  --out-pls pronunciation.pls
```

To update an existing project dictionary, add or replace rules by dictionary ID:

```bash
python scripts/build_pronunciation_dictionary.py pronunciation_glossary.csv \
  --update-dictionary-id DICTIONARY_ID \
  --out-manifest pronunciation_dictionary_manifest.json
```

Pass the returned locator to generation:

```bash
python scripts/generate_audio.py script.csv \
  --pronunciation-manifest pronunciation_dictionary_manifest.json \
  --voice host_a=VOICE_ID_A \
  --voice host_b=VOICE_ID_B
```

Use `--pronunciation-locator DICTIONARY_ID:VERSION_ID` when a locator is supplied directly instead of via a manifest.

Creating or updating a dictionary requires an ElevenLabs API key with pronunciation dictionary write permission. Generating audio with an existing dictionary locator uses the normal Text to Dialogue request path.

For high-risk terms, create a tiny pronunciation smoke-test script before generating the full episode. Keep the script text unchanged (`JELMYTO`, not raw IPA), pass the dictionary locator, generate one short clip, and listen before spending a full production run.

If a dictionary locator is unavailable:

- Prefer approved project glossary spellings.
- Use inline phonetic spellings only when they are approved for the spoken script.
- Do not silently alter brand or medical terms.
- Keep pronunciation notes in the `pronunciation` column until approved.
- Avoid uppercase pronunciation cues unless the term should be spelled as letters. ElevenLabs v3 can interpret all-caps tokens as acronyms and produce letter-by-letter readings.
- Do not classify brands, product names, or misspelled variants as `acronym` unless they should actually be read letter-by-letter; use a type such as `brand-or-product` for products that need normal word-style pronunciation.
- Prefer lowercase pronunciation payload cues for non-acronyms, for example `jel-my-toe` instead of an uppercase cue that could be read as `J-E-L`.
- After changing a glossary, run a dry-run payload and inspect the JSON for unwanted uppercase tokens, acronym-like spellings, or stale pronunciation substitutions before live generation.

If the project has an approved `pronunciation_glossary.csv`, pass it to the generator:

```bash
python scripts/generate_audio.py script.csv --pronunciation-glossary pronunciation_glossary.csv --voice host_a=VOICE_ID_A --voice host_b=VOICE_ID_B
```

This preserves the review script while applying term replacements only inside the ElevenLabs request payload, but it is a fallback. It cannot express IPA phonemes.

## Chunking

Prefer chunk mode for natural host interaction. Line mode is useful for smoke tests and surgical replacements, but it can sound choppy because each line is generated independently.

Reliable request limits:

- Keep the total character count across all `inputs[].text` values at or below 2,000 characters per request.
- Keep a chunk to a coherent conversational section, not an arbitrary equal-size slice.
- Avoid splitting setup/response pairs, short exchanges, or a heading and its first explanatory turn.
- Use one chunk directly when the whole test segment fits; do not merge a one-chunk output unless a normalized final export is needed.

Chunk filenames carry the covered line IDs:

```text
chunk_001-008.mp3
chunk_009-016.mp3
```

Use line mode for surgical replacement:

```text
001_host_a.mp3
002_host_b.mp3
```

Chunk boundaries should avoid splitting a setup and response when possible. When client edits land inside a chunk, regenerate that chunk and reuse unaffected chunks.

Editing model:

```text
reuse chunk_001-008.mp3
regenerate chunk_009-016.mp3
reuse chunk_017-024.mp3
merge chunks in manifest order
```

The script table keeps line-level IDs. The audio manifest maps each generated chunk to `id_start` and `id_end`, which is how changed rows identify the smallest affected chunk.
