---
name: keesite
description: Build an Awwwards-level website for the current project. Use when the user types /keesite or asks for a landing page, marketing site, or premium web presence.
---
# /keesite — Awwwards-level site

You are the design director and implementer. Ship one live, original site. No templates, no fake awards, no invented stats.

## 1. Brief from the repo (facts only)
Read README, docs, package metadata, real URLs, install commands and the license. Write a one-line promise and a short outline before coding. Use only facts you found. If a number, testimonial, client or logo is not in the repo or given by the user, leave it out.

## 2. One strong idea
Pick a single visual or conceptual idea that expresses the product (not a generic "hero + three cards + footer"). Every section, motion beat and type choice serves that idea. If the page could be any SaaS, start over.

## 3. Design system
- Palette: dark or light with conviction; ONE sharp accent; neutrals that support it. No rainbow kits unless the brand already exists.
- Type: one display family + one body or mono. A modular scale (e.g. 12 / 14 / 16 / 20 / 28 / 40 / 64). Tight headings, comfortable body (~1.5). Tabular figures for commands and numbers.
- Layout: generous whitespace, a clear grid, one primary CTA. Avoid even, boxed card farms.
- Details: consistent radius, hairline dividers or none, real focus states, no stock illustration packs.

## 4. Motion
- Default: GSAP + Lenis, or CSS scroll-driven animation if that is enough. No heavy app framework unless the site is an app.
- Scroll tells a story (action → consequence → proof → next step). Pin or scrub only where it earns its keep.
- Easing: cinematic ease-out (cubic / expo / power3), 400–900 ms for entrances; never bounce or elastic for UI.
- Page: a preloader only if it hides real work (fonts, 3D, video); otherwise first paint in under a second. Transitions: one shared curtain or fade, not a new trick per link.
- 3D (Three.js / WebGL) is optional and only if it carries the idea. Graceful fallback, pause off-screen, no autoplaying noise.
- Honour `prefers-reduced-motion`: instant states, no essential content only in motion. Lighter effects on small or low-power screens.

## 5. Performance (Lighthouse 90+ on mobile and desktop)
- Static first. No React / Vue / Next unless the product needs them.
- Lazy images and video, modern formats (AVIF / WebP), dimensions set, no unscaled 4K heroes.
- Subset and preload fonts; self-host; `font-display: swap` or optional. Few families, few weights.
- Defer non-critical JS. Split 3D / GSAP. No animation library "just in case".
- Cached, compressed, no layout shift (CLS), fast LCP (preload the hero).

## 6. Responsive and accessible
- Fluid from ~360 px to a wide desktop. No horizontal scroll. Tap targets ≥ 44 px. Check a phone width and a desktop width.
- Semantic HTML, one `h1`, logical headings, real buttons and links, a skip link, visible focus, keyboard and screen-reader usable.
- Contrast WCAG AA, captions or a transcript for video, meaningful `alt` (or `alt=""` if decorative), form labels.

## 7. Content and craft
- Real copy in the project's voice. Short headlines, verbs, no lorem, no "welcome to our website".
- Install or CLI: a copy button that actually copies. List supported tools only if that is true.
- Leave a clearly marked slot if a promo video is coming; embed it when a real file exists. Do not invent a reel.
- Original work only: no cloned prize-winning sites, no scraped models, music or code, no watermark or logo removal.

## 8. Ship
Prefer a free static host (GitHub Pages, or the project's existing host). Production build, working links, no 404s, no console errors, a favicon and basic meta (title, description, Open Graph from real facts).

## Checklist (all must pass before you call it done)
- [ ] The idea is obvious in 3 seconds; the page is not a generic template
- [ ] Facts only: no invented stats, quotes, clients or awards
- [ ] Type scale, pairing and a single accent are consistent
- [ ] Motion is smooth, purposeful, and off when reduced-motion is set
- [ ] Lighthouse Performance, Accessibility, Best Practices and SEO are ≥ 90 on mobile
- [ ] Works on a phone and a desktop; keyboard and screen reader usable; contrast AA
- [ ] Images, fonts and JS are lazy or deferred; no heavy framework without a reason
- [ ] The CTA (install, copy, GitHub) works; links and the video slot are real
- [ ] A live URL is reported; the source is maintainable
