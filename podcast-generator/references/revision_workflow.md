# Revision Workflow

Use this workflow when a client returns edits to a reviewed podcast script.

## Compare Tables

Compare the previous approved table and revised table by `id`. If `source_id` exists, prefer `source_id` for rows that have been production-renumbered.

Classify rows:

| classification | meaning |
| --- | --- |
| `unchanged` | Same host, text, and status-relevant fields. Reuse audio. |
| `changed` | Existing ID has different host or text. Regenerate affected line/chunk. |
| `inserted` | New ID appears in revised table. Generate affected line/chunk. |
| `removed` | Old ID is missing. Exclude from final unless deletion is accidental. |
| `skipped` | Revised status is `skip`. Exclude from final audio. |

Run:

```bash
python scripts/compare_revisions.py old_script.csv revised_script.csv --out revision_report.csv
```

## Regeneration Policy

- In line mode, regenerate changed and inserted rows only.
- In chunk mode, regenerate any chunk containing changed, inserted, removed, or skipped rows.
- Prefer chunk regeneration over line replacement for final-quality dialogue; it preserves organic pacing and avoids abrupt joins.
- Use `audio_manifest.csv` to map changed line IDs to the affected `id_start`/`id_end` chunk.
- If there is only one generated chunk, use that chunk directly as the episode segment. Merge is only needed for multiple chunks or normalized final exports.
- Preserve previous audio files by writing new outputs to a versioned directory such as `audio/v2/`.
- Update `audio_manifest.csv` after regeneration.

## Client Review Rules

- Never overwrite the only copy of a client-edited script.
- Keep client IDs stable during review.
- Renumber only when preparing production audio, and keep `source_id`.
- Treat `notes` as non-spoken comments.
- Treat bracketed text in `text` as spoken-generation direction for ElevenLabs v3, not a reviewer comment.
