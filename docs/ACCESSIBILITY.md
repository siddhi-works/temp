# Accessibility

## Standards we hold ourselves to

| Standard | How we use it |
|---|---|
| **WCAG 2.2 AAA** | Target wherever it applies: 7:1 text contrast, large targets, spacing, no timing, focus visible |
| **GIGW 3.0** (Indian government guidelines) | Reference: bilingual interface, accessibility and privacy statement (the About page), skip link, page titles |
| **British Dyslexia Association** | Reading comfort: sans-serif type, soft off-white backgrounds, no pure black on white, left-aligned text, 1.5+ line spacing, short paragraphs, no italics or all capitals, student-controlled colours and size |

We do **not** claim certification. Full AAA conformance across a whole site is not always possible, and no standard replaces a real user.

## Design choices that come from the research

- **One task per screen.** A screen reader reads everything in order; fewer elements means less to wade through.
- **Four colour themes**, chosen by the student: *Warm daylight*, *Soft sepia*, *Twilight dark*, *High contrast*. Dyslexia and low vision need different things, so a single palette cannot serve both.
- **Atkinson Hyperlegible** typeface (designed for low vision), 20px base, with Noto Sans Devanagari for Hindi and Marathi.
- **Three text sizes** and a **wide letter-spacing** option.
- **Targets of 56 to 76 px**, with 20 px between them.
- **Interface in English, Hindi and Marathi**, including errors, dates and the audio player.
- **A simple player:** Play or Pause, back 10 seconds, and speed (0.8, 1, 1.25). The browser's own player has about six stops.

## Contrast (computed from the actual colours)

| Pair | Daylight | Sepia | Twilight |
|---|---|---|---|
| Body text on page | 14.8 : 1 | 13.9 : 1 | 16.0 : 1 |
| Muted text on panels | 8.6 : 1 | 8.6 : 1 | 9.9 : 1 |
| Links on page | 9.6 : 1 | 9.1 : 1 | 9.4 : 1 |
| Control edges (3:1 needed) | 4.5 : 1 | 4.8 : 1 | 5.8 : 1 |
| High contrast theme | white on black, 21 : 1 | | |

AAA needs 7:1 for text. Before this review one palette fell short (muted text 6.5:1, links 5.9:1, box edges 1.6:1); all are fixed.

## How it is tested

`code/frontend/scripts/a11y-student.mjs` drives the real app in Chrome and, for every student screen and the About page, in four themes at normal and extra-large text:

1. runs **axe** with WCAG 2.0, 2.1, 2.2 and **AAA** rules and fails on serious or critical findings;
2. walks the keyboard: every stop needs a name and a visible focus ring;
3. checks every control is at least 44 px;
4. checks that each new screen moves focus to its title.

Also done by hand: the accessibility tree read as a screen reader would hear it, in English, Hindi and Marathi; 320 px width with extra-large text, high contrast and wide spacing (no clipping, no sideways scroll); and an eyes-closed pass on a phone with TalkBack.

## Known limits

- **No consented session with a blind or low-vision student yet.** This is the most important open item.
- Automated checks find roughly a third of issues; the rest need people.
- The staff desk has its own, older checks and is not covered by the student script.
- The browser's own file picker and date picker are used as they are.
