import Link from "next/link";
import Header from "@/components/Header";
import Braille from "@/components/Braille";

const FEATURES: [string, string][] = [
  ["Right now", "One button says which class is on, where, and what is due today. Also on WhatsApp: send NOW."],
  ["Clash radar", "When the college publishes a notice, each student hears only what clashes with their own classes."],
  ["Deadline catcher", "A teacher says “submit by next Friday”. One tap, say it, hear the date read back, and it becomes a reminder."],
  ["Board photos", "Photograph the board in class. Weeks later, ask “what was the Carnot formula?” and hear the answer."],
  ["Buzz codes", "A different vibration for a notice, a deadline, a clash and an exam, so the phone says what it is from the pocket."],
  ["Campus walkthroughs", "Rotaract volunteers record step-by-step walking routes. A QR code at the gate plays them."],
  ["Exam eve", "The night before an exam: the time, the hall, what to carry, and the route to the hall."],
];

export default function Home() {
  return (
    <>
      <Header wide />
      <main id="main" className="wrap-wide">
        <section className="hero" aria-labelledby="hero-title">
          <Braille word="saathi" className="braille" />
          <h1 id="hero-title">College, heard clearly.</h1>
          <p className="lede">
            Saathi Campus turns every college notice into a voice note and an accessible page, then goes further: it knows a blind
            student&rsquo;s timetable, catches deadlines said out loud in class, and remembers what was on the board.
          </p>
        </section>

        <nav className="doors" aria-label="Open Saathi Campus">
          <Link className="door" href="/me">
            <strong>Student app</strong>
            Open it from the link Saathi sends on WhatsApp.
          </Link>
          <Link className="door" href="/desk">
            <strong>College desk</strong>
            Upload notices, check dates, publish. Record walkthroughs.
          </Link>
          <Link className="door" href="/routes">
            <strong>Walkthroughs</strong>
            Play a walking route around campus.
          </Link>
        </nav>

        <section aria-labelledby="what">
          <h2 id="what">What a student gets</h2>
          <ul className="feature-list">
            {FEATURES.map(([name, text]) => (
              <li key={name}>
                <strong>{name}</strong>
                <span>{text}</span>
              </li>
            ))}
          </ul>
        </section>

        <section aria-labelledby="trust" style={{ marginTop: 34 }}>
          <h2 id="trust">Why students can trust it</h2>
          <p>
            Every date is checked against the original scan before a notice goes out, and a college staff member approves it. A
            date that does not match its weekday is held back until a person confirms it. Walking routes are recorded by one
            volunteer and checked by another person before anyone can play them.
          </p>
        </section>

        <footer className="foot">
          Built for Naya Bharat 2047, Code for Bharat, problem 5.3. Notices are read with Sarvam AI and Google Gemini, and voice
          notes are made with Sarvam Bulbul. Messages go through the official WhatsApp Business Platform.
        </footer>
      </main>
    </>
  );
}
