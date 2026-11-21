"use client";

/** Interface translations store (English, Hindi, Marathi) and friendly error messages. The same choice sets the voice language. */
/** Tiny UI translation store: English, Hindi, Marathi. The same choice sets the voice language for notices. */
import { useEffect, useState } from "react";
import { EXTRA } from "./i18n-extra";
import { MORE } from "./i18n-more";

export type Lang = "en" | "hi" | "mr";
export const LANGS: { code: Lang; name: string }[] = [
  { code: "en", name: "English" },
  { code: "hi", name: "हिन्दी" },
  { code: "mr", name: "मराठी" },
];

const D: Record<Lang, Record<string, string>> = {
  en: {
    home_title: "What would you like to do?",
    read: "Read a notice to me",
    read_sub: "A photo or PDF of a notice",
    catch: "Remember a deadline",
    catch_sub: "Say what the teacher said",
    deadlines: "My deadlines",
    deadlines_sub: "What you saved",
    settings: "Settings and my details",
    settings_sub: "Class, text size, colours, alerts",
    college: "College features",
    back: "Back",
    language: "Language",
    welcome_title: "Hear any college notice",
    welcome_lede: "Share a photo or PDF of a notice. Saathi reads it aloud in your language and checks the dates against the scan.",
    access_code: "Access code (only if the Saathi team gave you one)",
    start: "Start",
    starting: "Starting",
    welcome_privacy: "No account or phone number needed. Notices you read are not saved or shared.",
    session_expired: "Your saved session has expired. Please start again.",
    choose_file: "Choose a notice (photo or PDF)",
    reading: "Reading the notice. This takes up to a minute.",
    share_hint: "You can also share a notice to Saathi from WhatsApp or your gallery.",
    details_hint: "Add your class in Settings to hear what applies to you.",
    listen: "Listen",
    loading_voice: "Preparing the voice",
    speed: "Speed",
    for_you: "For you",
    nothing_for_you: "I could not find anything in this notice that is specifically for you.",
    check_note: "Checked against the scan. Please double-check on the notice itself.",
    found_in: "Found in the notice",
    summary: "What the notice says",
    dates_tasks: "Dates and tasks",
    not_confirmed: "Not confirmed against the scan. Please check with the college office.",
    read_another: "Read another notice",
    phone_voice: "Read aloud with my phone's voice",
    my_details: "My details",
    details_note: "Optional. Saved only on this phone. They are sent with a notice so Saathi can find your class, seat or roll number. The server does not keep them.",
    f_name: "Name",
    f_course: "Class, for example SY B.Com",
    f_division: "Division",
    f_roll: "Roll, seat or PRN number",
    f_batch: "Batch",
    f_extra: "Anything else, for example OBC scholarship",
    forget: "Forget my details",
    forgot: "Your details were removed from this phone.",
    display: "Display",
    text_size: "Text size",
    size_normal: "Normal",
    size_large: "Large",
    size_xlarge: "Extra large",
    colours: "Colours",
    look_calm: "Calm",
    look_dark: "Dark",
    look_contrast: "High contrast",
    spacing: "Letter spacing",
    space_normal: "Normal",
    space_wide: "Wide",
  },
  hi: {
    home_title: "आप क्या करना चाहते हैं?",
    read: "मुझे सूचना पढ़कर सुनाओ",
    read_sub: "सूचना की फ़ोटो या PDF",
    catch: "कोई डेडलाइन याद रखें",
    catch_sub: "जो शिक्षक ने कहा, बोलकर बताएँ",
    deadlines: "मेरी डेडलाइन",
    deadlines_sub: "जो आपने सेव किया",
    settings: "सेटिंग और मेरे विवरण",
    settings_sub: "कक्षा, अक्षर का आकार, रंग, अलर्ट",
    college: "कॉलेज की सुविधाएँ",
    back: "वापस",
    language: "भाषा",
    welcome_title: "कोई भी कॉलेज सूचना सुनें",
    welcome_lede: "सूचना की फ़ोटो या PDF साझा करें। Saathi उसे आपकी भाषा में पढ़कर सुनाता है और तारीखों को स्कैन से मिलाकर जाँचता है।",
    access_code: "एक्सेस कोड (केवल अगर Saathi टीम ने दिया हो)",
    start: "शुरू करें",
    starting: "शुरू हो रहा है",
    welcome_privacy: "किसी खाते या फ़ोन नंबर की ज़रूरत नहीं। आपकी पढ़ी गई सूचनाएँ सेव या साझा नहीं होतीं।",
    session_expired: "आपका सेव किया हुआ सत्र समाप्त हो गया है। कृपया फिर से शुरू करें।",
    choose_file: "सूचना चुनें (फ़ोटो या PDF)",
    reading: "सूचना पढ़ी जा रही है। इसमें एक मिनट तक लग सकता है।",
    share_hint: "आप WhatsApp या गैलरी से भी सूचना Saathi को शेयर कर सकते हैं।",
    details_hint: "आपके लिए क्या है यह सुनने के लिए सेटिंग में अपनी कक्षा जोड़ें।",
    listen: "सुनें",
    loading_voice: "आवाज़ तैयार हो रही है",
    speed: "गति",
    for_you: "आपके लिए",
    nothing_for_you: "मुझे इस सूचना में आपके लिए खास कुछ नहीं मिला।",
    check_note: "स्कैन से जाँचा गया। कृपया सूचना में खुद भी देख लें।",
    found_in: "सूचना में मिला",
    summary: "सूचना में क्या लिखा है",
    dates_tasks: "तारीखें और काम",
    not_confirmed: "स्कैन से पुष्टि नहीं हुई। कृपया कॉलेज कार्यालय से पूछें।",
    read_another: "दूसरी सूचना पढ़ें",
    phone_voice: "मेरे फ़ोन की आवाज़ में पढ़ें",
    my_details: "मेरे विवरण",
    details_note: "वैकल्पिक। केवल इसी फ़ोन में सेव रहते हैं। सूचना के साथ भेजे जाते हैं ताकि Saathi आपकी कक्षा, सीट या रोल नंबर ढूँढ सके। सर्वर इन्हें नहीं रखता।",
    f_name: "नाम",
    f_course: "कक्षा, जैसे SY B.Com",
    f_division: "डिवीज़न",
    f_roll: "रोल, सीट या PRN नंबर",
    f_batch: "बैच",
    f_extra: "और कुछ, जैसे OBC छात्रवृत्ति",
    forget: "मेरे विवरण मिटाएँ",
    forgot: "आपके विवरण इस फ़ोन से हटा दिए गए।",
    display: "डिस्प्ले",
    text_size: "अक्षर का आकार",
    size_normal: "सामान्य",
    size_large: "बड़ा",
    size_xlarge: "बहुत बड़ा",
    colours: "रंग",
    look_calm: "शांत",
    look_dark: "गहरा",
    look_contrast: "ज़्यादा कंट्रास्ट",
    spacing: "अक्षरों के बीच दूरी",
    space_normal: "सामान्य",
    space_wide: "ज़्यादा",
  },
  mr: {
    home_title: "तुम्हाला काय करायचे आहे?",
    read: "मला सूचना वाचून दाखवा",
    read_sub: "सूचनेचा फोटो किंवा PDF",
    catch: "एखादी अंतिम तारीख लक्षात ठेवा",
    catch_sub: "शिक्षकांनी जे सांगितले ते बोलून सांगा",
    deadlines: "माझ्या अंतिम तारखा",
    deadlines_sub: "तुम्ही सेव्ह केलेल्या",
    settings: "सेटिंग्ज आणि माझे तपशील",
    settings_sub: "वर्ग, अक्षरांचा आकार, रंग, अलर्ट",
    college: "महाविद्यालयाच्या सुविधा",
    back: "मागे",
    language: "भाषा",
    welcome_title: "कोणतीही महाविद्यालयीन सूचना ऐका",
    welcome_lede: "सूचनेचा फोटो किंवा PDF शेअर करा. Saathi ती तुमच्या भाषेत वाचून दाखवते आणि तारखा स्कॅनशी जुळवून तपासते.",
    access_code: "ॲक्सेस कोड (Saathi टीमने दिला असेल तरच)",
    start: "सुरू करा",
    starting: "सुरू होत आहे",
    welcome_privacy: "खाते किंवा फोन नंबरची गरज नाही. तुम्ही वाचलेल्या सूचना सेव्ह किंवा शेअर होत नाहीत.",
    session_expired: "तुमचे सेव्ह केलेले सत्र संपले आहे. कृपया पुन्हा सुरू करा.",
    choose_file: "सूचना निवडा (फोटो किंवा PDF)",
    reading: "सूचना वाचली जात आहे. यास एक मिनिट लागू शकतो.",
    share_hint: "तुम्ही WhatsApp किंवा गॅलरीतून सूचना Saathi ला शेअर करू शकता.",
    details_hint: "तुमच्यासाठी काय आहे हे ऐकण्यासाठी सेटिंग्जमध्ये तुमचा वर्ग जोडा.",
    listen: "ऐका",
    loading_voice: "आवाज तयार होत आहे",
    speed: "वेग",
    for_you: "तुमच्यासाठी",
    nothing_for_you: "मला या सूचनेत तुमच्यासाठी खास काही सापडले नाही.",
    check_note: "स्कॅनशी पडताळले. कृपया सूचनेत स्वतः तपासा.",
    found_in: "सूचनेत सापडले",
    summary: "सूचनेत काय लिहिले आहे",
    dates_tasks: "तारखा आणि कामे",
    not_confirmed: "स्कॅनशी खात्री झाली नाही. कृपया महाविद्यालयाच्या कार्यालयात विचारा.",
    read_another: "दुसरी सूचना वाचा",
    phone_voice: "माझ्या फोनच्या आवाजात वाचा",
    my_details: "माझे तपशील",
    details_note: "ऐच्छिक. फक्त याच फोनमध्ये सेव्ह राहतात. सूचनेसोबत पाठवले जातात, म्हणजे Saathi तुमचा वर्ग, आसन किंवा रोल नंबर शोधू शकेल. सर्व्हर ते ठेवत नाही.",
    f_name: "नाव",
    f_course: "वर्ग, उदा. SY B.Com",
    f_division: "तुकडी",
    f_roll: "रोल, आसन किंवा PRN क्रमांक",
    f_batch: "बॅच",
    f_extra: "आणखी काही, उदा. OBC शिष्यवृत्ती",
    forget: "माझे तपशील पुसून टाका",
    forgot: "तुमचे तपशील या फोनमधून काढले.",
    display: "डिस्प्ले",
    text_size: "अक्षरांचा आकार",
    size_normal: "सामान्य",
    size_large: "मोठा",
    size_xlarge: "खूप मोठा",
    colours: "रंग",
    look_calm: "शांत",
    look_dark: "गडद",
    look_contrast: "जास्त कॉन्ट्रास्ट",
    spacing: "अक्षरांमधील अंतर",
    space_normal: "सामान्य",
    space_wide: "जास्त",
  },
};

for (const l of Object.keys(EXTRA) as Lang[]) Object.assign(D[l], EXTRA[l], MORE[l]);

/** A friendly message in the chosen language for the errors the reading screens can meet; the server's own text is the fallback. */
export function friendlyError(e: unknown, t: (k: string) => string): string {
  const err = e as { code?: string; status?: number; message?: string } | null;
  const code = err?.code || "";
  if (err?.status === 429) return t("err_rate");
  if (code === "network") return t("err_network");
  if (code === "timeout") return t("err_timeout");
  const key = `err_${code}`;
  const msg = t(key);
  return msg !== key ? msg : err?.message || t("err_read_failed");
}

let current: Lang = "en";
let loaded = false;
const subs = new Set<() => void>();

function load() {
  if (loaded || typeof window === "undefined") return;
  loaded = true;
  try {
    const s = localStorage.getItem("saathi_ui") as Lang | null;
    if (s && D[s]) current = s;
  } catch {}
  document.documentElement.lang = current;
}

export function setLang(l: Lang) {
  current = l;
  try {
    localStorage.setItem("saathi_ui", l);
  } catch {}
  document.documentElement.lang = l;
  subs.forEach((f) => f());
}

export function useT() {
  const [, bump] = useState(0);
  useEffect(() => {
    load();
    bump((n) => n + 1);
    const f = () => bump((n) => n + 1);
    subs.add(f);
    return () => {
      subs.delete(f);
    };
  }, []);
  const t = (k: string) => D[current][k] ?? D.en[k] ?? k;
  return { t, lang: current, setLang };
}
