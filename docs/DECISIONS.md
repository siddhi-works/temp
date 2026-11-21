# Design decisions: why this, and why not that

Each entry: the decision, the alternative we rejected, and the trade-off we accepted. Written so a reviewer can disagree with a specific line.

## Product

**D1. Student-first, not college-first.** Colleges will not adopt a new admin tool for a student project, so a student can use Saathi alone on day one. A college desk exists as an optional second path.
*Rejected:* a dashboard the college must adopt. *Cost:* we cannot see or fix notices at the source.

**D2. No accounts.** A student taps Start and gets an anonymous session. There is nothing to sign up for, remember or leak.
*Rejected:* phone-number login (needs SMS cost and verification, and stores a phone number). *Cost:* a cleared browser loses the saved deadlines; abuse control relies on rate limits and an optional access code.

**D3. Personal details stay on the phone.** The student's class and roll number live in browser storage and travel inside the single request that needs them.
*Rejected:* server-side profiles. *Cost:* details do not follow the student to a new phone.

**D4. Four tasks on the home screen, nothing else.** Earlier builds had about twenty features on one page. A blind student's screen reader reads every element in order, so fewer, bigger, single-purpose screens won.
*Rejected:* a feature-rich dashboard. *Cost:* some staff-side features are hidden from the student view.

## Channel

**D5. Installable web app (PWA), not native, not an extension.** Opens from a link, installs to the home screen, appears in Android's Share menu, works with TalkBack, and needs no store review.
*Rejected:* native app (weeks, hand-labelled controls, store review), browser extension (not on phones, where our users are).

**D6. Web app before WhatsApp.** WhatsApp is where notices arrive, and our Share-menu flow starts from there. Sending replies through WhatsApp needs Meta business verification; unofficial libraries break WhatsApp's terms and put a real number at risk.
*Rejected:* an unofficial WhatsApp library. *Cost:* students forward through the Share menu, not to a number. The WhatsApp code exists and is switched off.

## AI

**D7. Treat the language model as untrusted.** It proposes; code verifies (see ARCHITECTURE.md section 3).
*Rejected:* trusting the model's dates. *Cost:* some correct dates get a warning when the scan is messy. We chose the safe direction.

**D8. Schema-enforced JSON.** The model must return a typed structure, validated on arrival.
*Rejected:* free text parsed with regular expressions.

**D9. Two vendors, three layers of fallback.** Sarvam is strongest on Indian-language scans and voice; Gemini adds vision and structure. Each job has a chain so one outage or quota end does not stop the student.
*Rejected:* a single vendor. *Cost:* two integrations to maintain. The adapters are small and tested with fakes.

**D10. Multiple keys and a second model.** Free quotas are per key and per model. Failover across both turned a real quota outage during testing into a slower answer instead of an error.

**D11. A hard time limit on the AI step.** The host cuts requests near 100 seconds, so the AI step stops at 40. The fallback quotes the scan, so the student always gets something true.

## Privacy

**D12. Nothing about a read is stored.** The staff path caches OCR text and audio for reuse; the student path bypasses both and returns audio inline (base64) rather than writing a file.
*Rejected:* caching to save money. *Cost:* repeated reads of the same notice cost the same each time (roughly ₹2).

## Engineering

**D13. One container, two processes.** Next.js in front, FastAPI behind it on localhost.
*Rejected:* separate services. *Benefit:* one origin (no CORS), one deploy, free hosting. *Cost:* both share 512 MB, so we limit concurrent reads to two.

**D14. SQLAlchemy over SQLite locally and Postgres in production.** Identical code, fast tests, and a free hosted database that survives restarts. A first deploy lost its data because the database setting was missing: now documented in DEPLOYMENT.md.

**D15. Layered code: routers, pipeline, providers.** HTTP, logic and vendors never mix, so the logic is testable without a server or a vendor.

**D16. A tiny custom translation store, not an i18n framework.** Three languages, client-side, no URL routing, and no extra bundle weight on a cheap phone.
*Cost:* strings are in TypeScript files, not a translation platform.

**D17. In-process job queue and rate limiter.** Right for one instance; replaced by a shared queue and store when we scale (ARCHITECTURE.md section 6).

## Accessibility

**D18. Colours are the student's choice.** Four themes, three sizes, two spacings, remembered on the phone. Dyslexia and low vision need opposite things (soft off-white versus maximum contrast), so one palette cannot serve both.

**D19. Real buttons and one control per action.** A hidden file input plus a styled label was announced twice by a screen reader; it is now one button.

**D20. Short paragraphs and one focused title per screen.** Results are split into one or two sentences each, and each screen moves focus to its title so the screen reader says where you are.
