/**
 * Add-to-calendar: a standard .ics file built on the phone from the dates that were checked against the scan.
 * Nothing is sent to a server. Android and iPhone open it in their own calendar, which then does the reminding
 * (the day before, and the morning of), so it works even when Samyukta is closed.
 */

export type CalAction = { label: string; due_date: string; due_time?: string | null; venue?: string | null };

const esc = (s: string) => s.replace(/\\/g, "\\\\").replace(/;/g, "\\;").replace(/,/g, "\\,").replace(/\r?\n/g, "\\n");
const pad = (n: number) => String(n).padStart(2, "0");

/** RFC 5545 asks for lines of at most 75 bytes. Fold by bytes so Hindi and Marathi text is never cut inside a letter. */
function fold(line: string): string {
  const enc = new TextEncoder();
  if (enc.encode(line).length <= 74) return line;
  const out: string[] = [];
  let cur = "";
  let bytes = 0;
  for (const ch of line) {
    const b = enc.encode(ch).length;
    if (bytes + b > (out.length ? 73 : 74)) {
      out.push(cur);
      cur = "";
      bytes = 0;
    }
    cur += ch;
    bytes += b;
  }
  out.push(cur);
  return out.join("\r\n ");
}

function utc(d: Date): string {
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}T${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}00Z`;
}

/** India has no daylight saving: IST is always UTC+5:30, so a clock time converts to UTC with one fixed offset. */
function istToUtc(date: string, time: string): Date {
  const [y, m, d] = date.split("-").map(Number);
  const [hh, mm] = time.split(":").map(Number);
  return new Date(Date.UTC(y, m - 1, d, hh, mm) - (5 * 60 + 30) * 60000);
}

export function buildIcs(actions: CalAction[], now: Date = new Date()): string {
  const L: string[] = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Samyukta//Notice dates//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"];
  actions.forEach((a, i) => {
    const [y, m, d] = a.due_date.split("-").map(Number);
    L.push("BEGIN:VEVENT", `UID:${a.due_date}-${i}-${now.getTime()}@samyukta`, `DTSTAMP:${utc(now)}`);
    if (a.due_time) {
      const start = istToUtc(a.due_date, a.due_time);
      L.push(`DTSTART:${utc(start)}`, `DTEND:${utc(new Date(start.getTime() + 3600000))}`);
    } else {
      const next = new Date(Date.UTC(y, m - 1, d + 1));
      L.push(`DTSTART;VALUE=DATE:${y}${pad(m)}${pad(d)}`, `DTEND;VALUE=DATE:${next.getUTCFullYear()}${pad(next.getUTCMonth() + 1)}${pad(next.getUTCDate())}`);
    }
    L.push(`SUMMARY:${esc(a.label)}`);
    if (a.venue) L.push(`LOCATION:${esc(a.venue)}`);
    L.push("DESCRIPTION:" + esc("Checked against the notice by Samyukta. Please confirm with the college office if anything changes."));
    // A day before, and the morning of (all-day events start at midnight, so 9 AM is 9 hours in).
    L.push("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", `TRIGGER:${a.due_time ? "-P1D" : "-PT15H"}`, "END:VALARM");
    L.push("BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Reminder", `TRIGGER:${a.due_time ? "-PT1H" : "PT9H"}`, "END:VALARM");
    L.push("END:VEVENT");
  });
  L.push("END:VCALENDAR");
  return L.map(fold).join("\r\n") + "\r\n";
}

export function downloadIcs(actions: CalAction[], filename = "samyukta-dates.ics") {
  const blob = new Blob([buildIcs(actions)], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}
