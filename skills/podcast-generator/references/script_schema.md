# Script Schema

Use the script table as the durable source of truth for drafting, review, revision, audio generation, and final assembly.

## Columns

Required:

| column | purpose |
| --- | --- |
| `id` | Stable review/order ID such as `001` or insertion ID such as `002.5`. |
| `host` | Speaker label such as `host_a`, `host_b`, or `narrator`. |
| `text` | Exact text to generate as spoken audio. |

Recommended:

| column | purpose |
| --- | --- |
| `status` | `draft`, `client-edited`, `approved`, `generated`, `regenerate`, or `skip`. |
| `notes` | Internal/client notes, unresolved questions, or audio direction. |
| `pronunciation` | Optional pronunciation note for this row only. |

Production:

| column | purpose |
| --- | --- |
| `production_id` | Clean sequential ID for final audio, generated from order. |
| `source_id` | Original review ID retained after renumbering. |
| `audio_file` | Generated line or chunk path. |
| `chunk_id` | Chunk grouping used for dialogue generation. |

## ID Rules

- Start with three digit IDs: `001`, `002`, `003`.
- Use decimal IDs for insertions during review: `002.5`, `002.1`, `002.2`.
- Sort IDs numerically by each dot-separated component.
- Before final production, use `scripts/renumber_script.py` to create clean `production_id` values.
- Preserve source IDs so client references remain traceable.

## Drafting Rules

- Keep each row to one natural spoken turn.
- Split long monologues into multiple rows.
- Preserve the exact approved wording in `text`.
- Put performance guidance intended for ElevenLabs v3 in square brackets only when it should influence audio generation.
- Use `notes` for comments that should not be spoken.
- Use `pronunciation` for review guidance, not as a replacement for the spoken text unless a phonetic rewrite is explicitly approved.

## Example

```csv
id,host,text,status,notes,pronunciation
001,host_a,"Welcome back. Today we are unpacking the new data.",approved,,
002,host_b,"Right, and the headline is not just efficacy. It is how the story fits clinical practice.",approved,,
002.5,host_a,"Let's pause on that, because this is where terminology really matters.",client-edited,Inserted by client,
003,host_b,"Exactly. The key term here may need a pronunciation check.",approved,,Check brand term
```
