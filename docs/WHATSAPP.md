# The WhatsApp assistant

A student can use Samyukta from WhatsApp as well as the website. Both call the same engine (`code/backend/app/pipeline/reader.py`), so a date is verified the same way in either place. It uses Meta's **official WhatsApp Cloud API** only: no WhatsApp Web pairing, no Baileys, whatsapp-web.js or Venom, and nobody is ever asked to scan a QR to share their own WhatsApp session.

## What a student can do

| Send | What happens |
|---|---|
| A photo or PDF of a notice | The notice is read, every date is checked against the scan, and the student gets a text reply and a voice note. Dates that could not be checked are marked "not confirmed" |
| A second notice within the hour | If it is about the same things as the first and a date moved, the student is told first, "it was 15 December, now 18 December". It is claimed only when both dates were verified against their own scans; anything unmatched or unverified claims nothing |
| A question, typed or as a voice note | Answered from the notice just sent, with the source line. Every date and number in the answer is checked against the notice |
| `SAVE 1` then `YES` | Saves the first checked date as a reminder. A date that could not be checked cannot be saved |
| `/deadlines` | Saved deadlines (and the college's, if the student joined one) |
| `/again` | Hear the last notice once more |
| `/language`, `hindi`, `marathi`, `english` | Change language |
| `/history` | Says plainly that no history is kept, and what is kept (see below) |
| `/link`, `/disconnect` | Connect or disconnect the website account |
| `/delete`, then `DELETE EVERYTHING` | Erases the student and everything held about them |
| `/stop`, `/start`, `/help` | Stop or resume reminders, show the commands |

Commands work with or without the `/`, in English, Hindi and Marathi.

## What is stored

- A WhatsApp student has an **encrypted phone number** and a language.
- The last notice read on WhatsApp is kept **encrypted for one hour** so the student can ask about it. The next notice replaces it, `DELETE` erases it, and it expires by itself. The file and the audio are never stored.
- The website keeps nothing: after a read it hands the phone an encrypted token, and the phone sends it back with each question.
- Only a deadline the student says YES to is saved, and only then are reminders scheduled.

## Connecting the website and WhatsApp

1. Settings, then WhatsApp, then **Connect WhatsApp**. The website shows an **Open WhatsApp** button and a QR code. Both are a `wa.me` link to the Samyukta number with a message already typed: "Hi Samyukta, my link code is K7M2QX4P". The code is random, works once and lasts 10 minutes. The QR holds no personal data.
2. The student presses Send. The assistant replies on WhatsApp with a **4-digit code**.
3. The student types that code on the website. Only then are the two connected.

A phone number alone never proves who someone is. Someone who photographs the QR and sends the code first gets a 4-digit code on their own phone, but cannot enter it in the real student's website session, and the website shows the last four digits of the number that claimed the code. Five wrong codes cancel it. A number already connected to one account cannot be connected to another until it is disconnected. The student can disconnect from the website or with `/disconnect` at any time.

Once connected, one account holds both. Saved deadlines appear in the WhatsApp `/deadlines`, and reminders go to WhatsApp.

## Setting it up (Meta)

1. At developers.facebook.com create an app of type **Business** and add the **WhatsApp** product.
2. **WhatsApp, API Setup:** copy the **Phone number ID**. For testing Meta gives a free test number and a 24-hour token; add the phones that will test it as recipients (Meta limits this list, and only listed phones can message a test number).
3. **A token that does not expire:** Business settings, System users, create one, give it the app and the WhatsApp account, and generate a token with `whatsapp_business_messaging` and `whatsapp_business_management`.
4. **App settings, Basic:** copy the **App secret**.
5. Set these on Render (`docs/DEPLOYMENT.md` lists the rest):

| Variable | Value |
|---|---|
| `WHATSAPP_TOKEN` | the token from step 3 |
| `WHATSAPP_PHONE_NUMBER_ID` | from step 2 |
| `WHATSAPP_APP_SECRET` | from step 4. Production refuses to start without it, and every webhook is checked against it |
| `WHATSAPP_VERIFY_TOKEN` | any long random string. Change it from the default |
| `WHATSAPP_BUSINESS_NUMBER` | the number students message, digits with country code, for example `919876543210` |

6. **WhatsApp, Configuration, Webhook:** callback URL `https://<your-render-url>/webhooks/whatsapp`, verify token as above, and subscribe to the **messages** field. The website already forwards `/webhooks/*` to the API.
7. Redeploy. The WhatsApp card appears in Settings only when `WHATSAPP_BUSINESS_NUMBER` is set, and the webhook is mounted whenever the token and phone number ID are set.

Tables are created at start-up, so no migration is needed for the new `link_code` and `recent_read` tables.

## Limits we know about

- **Not tested against Meta's live servers.** The Cloud API is faked in the tests, and the webhook, signature check, deduplication, media download and sending are real code that has run only against those fakes. The first live run on a test number is the real test. Do that before any demo.
- **24-hour window.** A free-form message can only go to a student who messaged within 24 hours. A reminder to a student outside that window needs an approved **utility template** (`WHATSAPP_NOTICE_TEMPLATE`, with three body parameters: title, text, link). Without one, the reminder is sent only as a phone vibration alert, and a student who has neither will not get it.
- **A new number needs Meta's business verification** before it can message the public. Until then only test recipients can use it.
- Meta's current rules for AI assistants on WhatsApp should be re-read before a public launch. This one answers questions about the notice a student sent, which is a narrow, task-specific use, but the rules change.
- Voice notes are converted to OGG/Opus with ffmpeg (it is in the Docker image). If ffmpeg is missing, an MP3 is sent as a normal audio message.
- Inbound voice questions use Sarvam speech-to-text, then Gemini audio. Speech-to-text of Marathi in a noisy room has not been measured.
- Message status webhooks (sent, delivered, read) are recorded for the college path. Student-mode replies are not tracked.
- Rate limiting is a daily cap per student, plus the access code and the spend cap. It lives in the database, so it survives a restart.

## Business App Coexistence (investigated, not built)

Coexistence is Meta's option that lets a business keep using the **WhatsApp Business app** on a number while the same number is connected to the Cloud API. A business connects it through Meta's **Embedded Signup**, where the business owner scans a QR code shown in the flow with their WhatsApp Business app.

It is **a different thing from the student QR.** The student QR opens a chat with Samyukta. The Coexistence QR connects a business owner's own number to the API. They must never be shown on the same screen.

We did not build it because:

- It is for a business that already has a WhatsApp Business app number. A student project does not, and a single number registered directly on the Cloud API is simpler.
- Embedded Signup needs Meta app review and, for onboarding other businesses, Tech Provider status. That cannot be tested in a hackathon.
- Eligibility depends on the country, the app version, how long and how actively the number has been used, and Meta's current rules. We could not check these from where this was written. **Do not promise Coexistence to anyone** until Meta's documentation and your own account confirm it.

If a college wants to run Samyukta on its own existing Business-app number, that is the case it would serve. Treat it as a later, optional business-owner onboarding feature behind the college side, not part of the student experience.
