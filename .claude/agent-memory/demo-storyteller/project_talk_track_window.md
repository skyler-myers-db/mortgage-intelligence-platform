---
name: Talk-track target is 6–8 min booth pitch, counted in blockquoted lines
description: The DAIS talk track must fit 6–8 minutes of wall clock; time it by counting spoken blockquote words at 150-170 wpm
type: project
---

`docs/module0-talk-track.md` targets a 6–8 minute booth pitch (plus 45s
open + 30s close). Only lines beginning with `> ` (markdown blockquote)
count as spoken; stage directions in italics inside blockquotes are skipped.

**Why:** DAIS booth slots are tight and the audience churns; overshooting
8 min means the closer gets cut and the three asks at the end don't land.

**How to apply:** Time-check with
`awk '/^> /' docs/module0-talk-track.md | wc -w`.
Target 1050–1400 spoken words. Under that = thin story; over that = will
overrun at demo pace. At an energetic demo cadence of ~170 wpm, 1200 words
lands at about 7 minutes — the sweet spot.
