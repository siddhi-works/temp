# Questions judges are likely to ask, and straight answers

Numbers marked *measured* come from our own runs on real notices; *estimated* come from configured prices.

## The product

**1. Why not paste the PDF into ChatGPT or Gemini?**
A blind student cannot operate that flow, it does not read scans in Marathi reliably, and nothing checks its dates. Samyukta is one tap from the Share menu, speaks the result, and verifies every date against the scan. A confident wrong date is the dangerous failure.

**2. How do you stop the AI inventing a deadline?**
We treat it as untrusted. Every date, weekday, amount, time and quoted line is re-checked in code against the scan. If anything fails, Samyukta still speaks but says "please confirm with the college office". (Architecture, section 3.)

**3. Who is it for, and who pays?**
Blind, low-vision and dyslexic students in colleges that publish scanned notices. A Rotaract club or a college disability cell can run it: about ₹2 per notice (*estimated*: ₹0.50 per scanned page plus ₹3 per 1,000 spoken characters; text PDFs skip the scan cost).

**4. Why a web app and not a native app?**
It opens from a link, installs to the home screen, appears in Android's Share menu, and TalkBack reads it natively. No store review, one codebase. Native would cost weeks and hand-labelled controls.

**5. Why not WhatsApp?**
We are on WhatsApp, through Meta's official Cloud API only (no WhatsApp Web pairing, no unofficial library). A student sends a notice to the Samyukta number and gets the same verified reading, as text and a voice note, and can ask questions about it. The website and WhatsApp can be linked with a QR code and a two-step check, so deadlines and reminders follow the student. It runs when the number's credentials are set; until Meta approves a number, only registered test numbers can use it.

**5b. Is there an admin or staff side?**
The code for a college-side desk exists, but the product is deliberately student-only: it is switched off, and while off its endpoints do not exist. That removes a whole attack surface (including admin setup) and keeps the demo about the student. It can be switched on for colleges that want human-approved official notices.

**5b. How do you protect students from fake fee notices?**
A safety check reads the notice for payment to a personal UPI ID or mobile number, shortened or look-alike links, OTP or PIN requests and "pay within 24 hours" wording. It says what it found and to confirm with the college office. It never calls a notice fake, because real ones also ask for fees. It is code, not AI, and it has tests for the false alarms (an email address, a contact number, "never share your OTP"). It will miss fraud that uses no pattern we know.

**5c. How does a student know a date is real?**
Each checked date shows the exact line of the scan it came from, can be read aloud, and can be added to the phone's calendar. A date we could not check shows no proof, says "not confirmed" in words and an icon, and cannot be saved as a reminder.

## Privacy and security

**6. What do you store?**
For a read on the website: nothing. On WhatsApp: the notice is kept encrypted for one hour so the student can ask about it, then erased. The file, the summary and the audio are never stored. If a student links WhatsApp we store their phone number, encrypted, until they disconnect or send DELETE. A deadline the student chooses to save is stored. (Security, section 3.)

**7. How is personalisation private?**
Class and roll number live on the phone and travel only inside the request that needs them. The server never writes them, and the AI's answer about "you" is accepted only if the quoted line names the student's own roll number, name or class.

**8. What about prompt injection in a malicious notice?**
Scan text is delimited as untrusted data, output must match a schema, the student's details are sanitised, and nothing the AI returns is spoken as fact without a code check. A notice that says "ignore your rules" cannot change a verified date.

**9. How do anonymous users not abuse it?**
Per-student read limits, an optional access code, a setup lock, and a hard spend cap. Limits are per student, so a whole classroom on one Wi-Fi is not blocked. (Tested.)

**10. Is it compliant with India's data protection law?**
We designed to avoid collecting personal data for reads, which is the strongest position. We have not had legal review and do not claim compliance.

## Engineering

**11. What happens when the AI is down or out of quota?**
It happened to us during testing. Understanding fails over across keys and then to a second model; reading fails over from Sarvam to Gemini; voice from Sarvam to Google to the phone's own voice. If all AI fails, Samyukta quotes the scan text and any dates it finds, flagged for checking.

**12. How fast is it?**
*Measured:* about 7 to 25 seconds for a one-page notice, up to about a minute for long PDFs. A hard 40-second limit on the AI step keeps it inside the host's request limit.

**13. Will it scale?**
The student path is stateless, so scaling is adding instances and AI quota. Today the job queue and rate limiter are in-process, which is right for one instance; the 100x plan (shared queue, migrations, object storage) is written down. The first real limit is AI provider quota and cost, not our code.

**14. Why two AI vendors? Isn't that lock-in risk reversed?**
Sarvam is built for Indian languages and voice; Gemini adds vision and structured output. Each is behind a small adapter and a failover chain, so either can be swapped or lost without touching the logic.

**15. How is it tested?**
179 automated tests: the full flow, injected failures (provider outages, quota errors), performance budgets, security guardrails, and personal insight (including "a claim about someone else's row is dropped"). Plus an accessibility test that runs axe, keyboard and size checks on every screen in every theme.

**16. What is your CI?**
A GitHub Actions workflow runs the backend tests, lints, type-checks and builds the frontend on every push.

## Accessibility

**17. How do you know it is accessible?**
Computed 7:1 contrast on four palettes, axe including AAA rules across every screen, keyboard and target checks, and eyes-closed TalkBack passes. We are explicit that automated tests are not enough.

**18. Has a blind student used it?**
Not yet in a formal, consented session. It is the next milestone and we say so in the README.

**19. Why three languages and not twenty?**
Marathi, Hindi and English cover our pilot's students. The interface, dates, errors and voice are localised through small dictionaries, so adding a language means translating about 150 strings and checking the voice and OCR for it.

## Honesty questions

**20. What is the biggest weakness?**
No real-user study yet, and the free AI tiers are small. Both are named, and both have a plan.

**21. What would you build next?**
A consented study with blind students, then WhatsApp delivery when business verification is possible, calendar reminders, and a revision and quiz mode that summarises lecture notes by voice.

**22. Did you use AI to build it?**
Yes: an AI coding assistant helped write code. Every behaviour is covered by automated tests and reviewed by the team. The AI services used in the product are Sarvam and Gemini.
