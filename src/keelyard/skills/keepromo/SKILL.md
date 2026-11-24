---
name: keepromo
description: Make a premium promo ad (16:9) and short vertical stories (9:16) for the current project. Use when the user types /keepromo or asks for a promo video, ad, reel or stories.
---
# /keepromo — promo ad + vertical stories

Goal: one 20–30 s 16:9 ad and three 8–12 s 9:16 stories that look like premium motion design, never like a slideshow or an AI video.

## 1. Facts first (no inventing)
Read README, package metadata, CLI help and docs. Extract: name, one-line promise, the pain it removes, 3 features, install command, URL. Use ONLY real facts. Never invent numbers, users, stars, testimonials or logos. If a fact is missing, leave it out.

## 2. Script structure
- Ad (20–30 s): Hook (0–3 s, the pain shown, not told) → Problem (3–8 s) → Product steps in (8–18 s, show it working) → Payoff (18–24 s) → CTA (last 4–6 s: install command + URL, on screen ≥3 s).
- Story (8–12 s, one feature each): Hook line (0–2 s) → feature demo (2–8 s) → CTA (last 2–3 s).
- Max ~6 words per on-screen line. One idea per shot. Big verbs.

## 3. Shot list (write it before building)
Table: `# | start–end | visual | on-screen text | motion | sound hit`. Shots 1.5–4 s. Every cut lands on a music beat; leave breathing room after big hits.

## 4. Motion style
- Dark background, ONE sharp accent colour (+ success green / error red only when meaning it).
- Kinetic typography: words slide/scale/mask in with ease-out (cubic/expo), 6–12 frame entrances, staggered per word.
- Real product visuals: terminal typing, code diffs, CLI output — use actual commands from the project.
- Depth: subtle 3D camera push, parallax, glow on accent, soft grain/vignette. No cheap effects (no wipes, star transitions, bouncy clip-art).
- Consistent: same fonts (1 display + 1 mono), same easing, same accent everywhere.

## 5. Captions & text
Safe areas: 16:9 keep text inside 90%; 9:16 keep clear top 250 px and bottom 400 px (platform UI). Min font ~48 px at 1080 wide. High contrast. Spell-check every string. If there is voiceover, burn word-synced captions; otherwise the typography IS the caption.

## 6. Audio
Copyright-free music from the internet (Kevin MacLeod/incompetech CC BY, Pixabay Music, NCS) — never generated. Write MUSIC_CREDIT.txt. Loudness ≈ −14 LUFS, true peak ≤ −1 dBTP. Fade out last 1 s.

## 7. Formats & export
- Ad: 1920×1080, 30 fps, H.264 high, yuv420p, AAC 192k.
- Stories: 1080×1920, 30 fps, same codecs.
- Web hero: 1280×720 copy, `-movflags +faststart`, ≤ 8 MB, works muted.
- Names: `<project>_promo_16x9_1080p.mp4`, `<project>_story_<feature>.mp4`, cover PNG.

## 8. Build (tool-agnostic)
Prefer code-driven video: Remotion (React) → `npx remotion render`; else HTML/CSS/canvas frames + ffmpeg; else Python (moviepy/PIL) + ffmpeg. Steps: scaffold → constants (colors, fonts, timings from shot list) → one component per shot → audio → render → review.

## 9. Review (mandatory before delivering)
Extract a frame every ~1 s (`ffmpeg -vf fps=1`) and check: typos, clipped/overflowing text, safe areas, accent consistency, CTA readable, cuts on beat, no black/blank frames, audio level. Fix and re-render until clean.

## 10. No video tool available
Deliver instead: the script, full shot list with timings, all on-screen copy, style sheet (hex colours, fonts, easing), music pick + credit, and a ready-to-run Remotion or ffmpeg project/commands the user can render later. Say clearly that no video was rendered.

## Output
Report only file paths + durations (or the plan files if not rendered).
