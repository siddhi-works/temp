/**
 * A built-in sample notice, so anyone can see what a result looks like without a file, an account or any AI quota.
 * It is shaped exactly like a real answer from /api/me/read, and the screen labels it as a sample. Dates are relative to today.
 * It has no audio (the phone's own voice reads it) and no "Ask" box, because no real notice stands behind it.
 */

import type { Lang } from "./i18n";

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const add = (n: number) => { const d = new Date(); d.setHours(12, 0, 0, 0); d.setDate(d.getDate() + n); return d; };
const long = (d: Date, lang: Lang) => d.toLocaleDateString(lang === "mr" ? "mr-IN-u-nu-deva" : `${lang}-IN`, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const dmy = (d: Date) => `${String(d.getDate()).padStart(2, "0")}/${String(d.getMonth() + 1).padStart(2, "0")}/${d.getFullYear()}`;

const WORDS: Record<Lang, Record<string, string>> = {
  en: { title: "Last date for the exam form", form: "Submit the exam form", hall: "Collect your hall ticket", hostel: "Submit the hostel form", room: "Room 14", spoken: "Submit your exam form and pay the fee. Collect your hall ticket from Room 14.", upi: "This notice asks you to pay to the UPI ID example dot fee at ybl. Before you pay, confirm this ID with the college office.", q1: "Last date for exam form submission:", q2: "Hall tickets will be distributed on" },
  hi: { title: "परीक्षा फ़ॉर्म की अंतिम तारीख़", form: "परीक्षा फ़ॉर्म भरें", hall: "हॉल टिकट लें", hostel: "छात्रावास फ़ॉर्म जमा करें", room: "कमरा 14", spoken: "परीक्षा फ़ॉर्म भरें और शुल्क जमा करें। हॉल टिकट कमरा 14 से लें।", upi: "इस सूचना में UPI आईडी example dot fee at ybl पर पैसे भेजने को कहा गया है। पैसे भेजने से पहले कॉलेज कार्यालय से यह आईडी पक्की कर लें।", q1: "परीक्षा फ़ॉर्म जमा करने की अंतिम तारीख़:", q2: "हॉल टिकट बाँटे जाएँगे" },
  mr: { title: "परीक्षा अर्ज भरण्याची अंतिम तारीख", form: "परीक्षा अर्ज भरा", hall: "हॉल तिकीट घ्या", hostel: "वसतिगृह अर्ज जमा करा", room: "खोली 14", spoken: "परीक्षा अर्ज भरा आणि शुल्क जमा करा. हॉल तिकीट खोली 14 मध्ये मिळेल.", upi: "या सूचनेत UPI आयडी example dot fee at ybl वर पैसे पाठवायला सांगितले आहे. पैसे पाठवण्यापूर्वी महाविद्यालयाच्या कार्यालयात हा आयडी खात्री करून घ्या.", q1: "परीक्षा अर्ज भरण्याची अंतिम तारीख:", q2: "हॉल तिकीट वाटले जाईल" },
};

export function sampleResult(lang: Lang) {
  const w = WORDS[lang], due = add(10), hall = add(14);
  return {
    sample: true,
    title: w.title,
    is_notice: true,
    lang,
    legibility: "good",
    needs_check: true,
    personal: null,
    audio_b64: null,
    audio_mime: "audio/mpeg",
    ask_token: null,
    spoken_text: w.spoken,
    safety: [{ kind: "upi", level: "caution", text: w.upi, evidence: "example.fee@ybl" }],
    actions: [
      { label: w.form, due_date: iso(due), date_spoken: long(due, lang), due_time: null, venue: null, status: "verified", evidence: `${w.q1} ${dmy(due)} (${due.toLocaleDateString(lang === "mr" ? "mr-IN" : `${lang}-IN`, { weekday: "long" })}).` },
      { label: w.hall, due_date: iso(hall), date_spoken: long(hall, lang), due_time: "10:30", venue: w.room, status: "verified", evidence: `${w.q2} ${dmy(hall)} 10.30 am, ${w.room}.` },
      { label: w.hostel, due_date: iso(add(23)), date_spoken: null, due_time: null, venue: null, status: "needs_check", evidence: null },
    ],
  };
}
