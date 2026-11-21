# Saathi Campus web app

Next.js 15 frontend for the Saathi Campus backend (`../backend`). It proxies `/api`, `/media` and `/n` to the backend, so the
whole product runs on one domain and student links from WhatsApp open here.

| Screen | Path | Who |
| --- | --- | --- |
| Landing | `/` | Everyone, judges |
| Student app | `/me?t=…` (link sent by the bot: INBOX) | Blind and low-vision students |
| Walkthrough player | `/r/{id}` (QR code target) | Students |
| Walkthrough list | `/routes?c={college_id}` (bot: ROUTE) | Students |
| College desk | `/desk` | Staff and Rotaract volunteers |
| Route recorder | `/desk/routes/{id}` | Volunteers |
| Accessible notice page | `/n/{id}` (served by the backend) | Students |

## Run

```bash
cp .env.example .env.local      # BACKEND_URL=http://localhost:8000
npm install
npm run build && npm start      # http://localhost:3000
```

The backend's `FRONTEND_BASE_URL` must point at this app so links in WhatsApp messages open it.

## Accessibility

Design choices: Atkinson Hyperlegible Next (made for low-vision readers), 19px base text, 56px+ touch targets,
yellow-on-ink high contrast with a full dark theme, 4px focus rings, one landmark structure on every page, live regions
for every result, focus moved to new content (the date read-back, each walkthrough step), and no information shown by
colour alone. Vibration ("Buzz Codes") works on Android Chrome only; iPhones get normal notifications.

### Automated check (run before every demo)

Backend on :8000 with an **empty** test database, this app on :3000, then:

```bash
npm run test:a11y      # writes a11y-report/ with screenshots and report.json
```

It drives every screen like a user (setup, add student, record a route, play it, add a class, catch a deadline), runs axe
(WCAG 2.2 AA + best practice) in light and dark mode, tabs through pages checking focus rings and names, and checks that
focus moves to the read-back and to each walkthrough step. Last run: no violations on any screen.

The desk has a **Team** tab (admin only) that creates sign-in tokens for staff and Rotaract volunteers.

### Manual screen reader test (required: automated tools catch only part of the problems)

Do this on a real Android phone with **TalkBack** and on Windows with **NVDA + Chrome**, ideally with a blind student.
Record the session for the backup demo.

1. Open the student link. Swipe through: the first stop after the header is the yellow "What's on right now?" button.
2. Double-tap it. The answer is read out without moving focus.
3. "Catch a deadline": type or record "assignment due next Friday". Focus jumps to the read-back sentence; the next swipe
   reaches the date field, then "Save reminder". Save, and hear "Saved: …".
4. "Photograph the board": the camera opens. After saving, the status is read out when the board has been read.
5. Headings navigation (TalkBack: swipe up/down to Headings; NVDA: H) lists every section in order.
6. Open a walkthrough. "Start walking", then each "Next" reads the new step aloud. Check the voice clip and the screen
   reader do not talk over each other; if they do, untick "Play the volunteer's voice".
7. Switch language to Marathi in Settings and open a notice: Marathi text is read with a Marathi voice (`lang="mr"`).
8. Zoom the page to 200% and turn on the phone's large text: nothing overlaps or scrolls sideways.
