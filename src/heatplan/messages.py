"""Worker and officer message templates (Hindi + English).

Messages are templated, not generated: health guidance must be exact and reviewable.
"""
from __future__ import annotations

from datetime import datetime

from .plan import BANDS

EMERGENCY = "112"
TIER_EMOJI = {"green": "🟢", "yellow": "🟡", "orange": "🟠", "red": "🔴"}
BAND_EMOJI = ["🟢", "🟡", "🟠", "🔴", "⛔"]

REPORT_TYPES = {
    "water": {"role": "water", "en": "🚱 No drinking water at site", "hi": "🚱 साइट पर पीने का पानी नहीं"},
    "shade": {"role": "disaster", "en": "⛱️ No shade / rest place", "hi": "⛱️ छाँव / आराम की जगह नहीं"},
    "work": {"role": "labour", "en": "⛔ Heavy work not stopped", "hi": "⛔ भारी काम नहीं रोका गया"},
    "ill": {"role": "health", "en": "🤒 Someone feels unwell", "hi": "🤒 किसी की तबीयत खराब"},
}
STATUS_BUTTON = {"en": "📍 Today's heat", "hi": "📍 आज की गर्मी"}


def _h12(hhmm: str) -> int:
    h = int(hhmm[:2]) % 12
    return 12 if h == 0 else h


def _part_of_day_hi(hhmm: str) -> str:
    h = int(hhmm[:2])
    if 4 <= h < 12:
        return "सुबह"
    if 12 <= h < 16:
        return "दोपहर"
    if 16 <= h < 20:
        return "शाम"
    return "रात"


def window_hi(start: str, end: str) -> str:
    return f"{_part_of_day_hi(start)} {_h12(start)} बजे से {_part_of_day_hi(end)} {_h12(end)} बजे तक"


def window_en(start: str, end: str) -> str:
    def f(x):
        h = int(x[:2])
        return f"{_h12(x)} {'AM' if h < 12 else 'PM'}"
    return f"{f(start)} to {f(end)}"


def date_label(day: str, lang: str) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    if lang == "hi":
        months = ["जनवरी", "फ़रवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"]
        return f"{d.day} {months[d.month - 1]} {d.year}"
    return d.strftime("%-d %b %Y")


def worker_alert(zr: dict, zone: dict, day: str, lang: str, replay: bool = False) -> dict:
    """Returns {'text': ..., 'voice': ...} for a worker in this zone."""
    b = BANDS[zr["band"]]
    tag = ("[अभ्यास / REPLAY] " if lang == "hi" else "[REPLAY] ") if replay else ""
    if lang == "hi":
        head = f"{tag}{BAND_EMOJI[zr['band']]} गर्मी चेतावनी — {zone['name_hi']}\n{date_label(day, 'hi')}"
        when = window_hi(zr["start"], zr["end"])
        if zr["band"] >= 4:
            rule = f"⛔ {when} भारी काम बंद रखें। हल्का काम सिर्फ छाँव में करें।"
            rule_v = f"{when} भारी काम बंद रखें। हल्का काम सिर्फ छाँव में करें।"
        else:
            rule = f"👷 {when}: हर घंटे {b['work']} मिनट काम, {b['rest']} मिनट छाँव में आराम।"
            rule_v = f"{when}, हर घंटे {b['work']} मिनट काम करें और {b['rest']} मिनट छाँव में आराम करें।"
        night = "\n🌙 रात में भी गर्मी कम नहीं हुई — शरीर थका हुआ है, ज़्यादा सावधानी रखें।" if zr.get("hot_night") else ""
        text = (f"{head}\nखतरा: {b['label_hi']} (WBGT {zr['peak_wbgt']}°C)\n\n{rule}\n"
                f"💧 हर 15–20 मिनट पानी पिएँ, प्यास न लगे तब भी।{night}\n"
                f"🚨 चक्कर, उल्टी, तेज़ सिरदर्द या बेहोशी हो तो तुरंत छाँव में ले जाएँ, ठंडा पानी डालें और {EMERGENCY} पर कॉल करें।")
        voice = (f"{zone['name_hi']} के लिए गर्मी की चेतावनी। आज {b['label_hi']}। {rule_v} "
                 f"हर पंद्रह से बीस मिनट में पानी पिएँ। चक्कर या उल्टी हो तो तुरंत छाँव में जाएँ और एक सौ बारह पर कॉल करें।")
    else:
        head = f"{tag}{BAND_EMOJI[zr['band']]} Heat alert — {zone['name']}\n{date_label(day, 'en')}"
        when = window_en(zr["start"], zr["end"])
        if zr["band"] >= 4:
            rule = f"⛔ {when}: stop heavy work. Light work in shade only."
        else:
            rule = f"👷 {when}: every hour, {b['work']} min work, {b['rest']} min rest in shade."
        night = "\n🌙 The night did not cool down — bodies have not recovered; take extra care." if zr.get("hot_night") else ""
        text = (f"{head}\nRisk: {b['label']} (WBGT {zr['peak_wbgt']}°C)\n\n{rule}\n"
                f"💧 Drink water every 15–20 minutes, even if not thirsty.{night}\n"
                f"🚨 Dizziness, vomiting, severe headache or fainting: move to shade, cool with water, call {EMERGENCY}.")
        voice = text
    return {"text": text, "voice": voice}


def status_text(zr: dict, zone: dict, day: str, lang: str) -> str:
    if not zr or not zr.get("available"):
        return "अभी जानकारी उपलब्ध नहीं।" if lang == "hi" else "No data yet."
    if zr["band"] == 0:
        if lang == "hi":
            return f"🟢 {zone['name_hi']}, {date_label(day, 'hi')}: आज गर्मी का खतरा कम है (WBGT {zr['peak_wbgt']}°C)। पानी पीते रहें।"
        return f"🟢 {zone['name']}, {date_label(day, 'en')}: low heat risk today (WBGT {zr['peak_wbgt']}°C). Keep drinking water."
    return worker_alert(zr, zone, day, lang)["text"]


def officer_action(action: dict, city: dict, zone: dict, zr: dict, replay: bool = False) -> str:
    tag = "[REPLAY] " if replay else ""
    return (f"{tag}{TIER_EMOJI[zr['tier']]} ACTION · {city['name']} · {zone['name']}\n\n"
            f"{action['text_rendered']}\n\n"
            f"Why: Tmax {zr['tmax']}°C → {zr['tier'].upper()} tier"
            f"{' (raised: hot night ' + str(zr['night_min']) + '°C)' if zr.get('hot_night') else ''}; "
            f"WBGT peak {zr['peak_wbgt']}°C at {zr['peak_hour']:02d}:00 → band {zr['band']}.\n"
            f"Plan: {action['ref']}\n\nTap Done once it is in place. It goes on the public log.")


def officer_report(rep: dict, city: dict, zone: dict) -> str:
    t = REPORT_TYPES[rep["type"]]
    return (f"📣 WORKER REPORT · {city['name']} · {zone['name']}\n{t['en']}\n"
            f"Reported at {rep['time']} IST. Tap Resolved once handled.")


ILL_REPLY = {
    "hi": (f"🚨 तुरंत: व्यक्ति को छाँव में ले जाएँ, कपड़े ढीले करें, ठंडा पानी डालें और हवा करें। "
           f"होश में हो तो घूँट-घूँट पानी दें। बेहोशी, भ्रम या उल्टी हो तो अभी {EMERGENCY} पर कॉल करें। "
           "आपकी रिपोर्ट स्वास्थ्य अधिकारी को भेज दी गई है।"),
    "en": (f"🚨 Now: move the person to shade, loosen clothing, pour cool water and fan them. "
           f"Give sips of water if conscious. If confused, fainting or vomiting, call {EMERGENCY} now. "
           "Your report has been sent to the health officer."),
}
REPORT_THANKS = {
    "hi": "✅ आपकी रिपोर्ट अधिकारी को भेज दी गई है। धन्यवाद।",
    "en": "✅ Your report has been sent to the responsible officer. Thank you.",
}
