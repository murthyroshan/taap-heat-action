"""Printable A4 poster (Hindi first) for labour chowks and site notice boards."""
from __future__ import annotations

from html import escape

from . import messages
from .plan import BANDS, load_city, zone_by_id

COLOURS = ["#2e7d32", "#f9a825", "#ef6c00", "#c62828", "#4a148c"]


def build(snap: dict, zone_id: str) -> str:
    city = load_city(snap["city"])
    zone = zone_by_id(city, zone_id)
    zr = next((z for z in snap["zones"] if z["zone"] == zone_id), None)
    if not zone or not zr or not zr.get("available"):
        return "<p>No data for this zone.</p>"
    b = BANDS[zr["band"]]
    rows = []
    for h in zr["hourly"]:
        if not 6 <= h["hour"] <= 19:
            continue
        hb = BANDS[h["band"]]
        rule_hi = "भारी काम बंद" if h["band"] >= 4 else ("सामान्य काम" if h["band"] == 0 else f"{hb['work']} मिनट काम / {hb['rest']} मिनट आराम")
        rule_en = "Stop heavy work" if h["band"] >= 4 else ("Normal work" if h["band"] == 0 else f"{hb['work']} min work / {hb['rest']} min rest")
        rows.append(f"<tr><td class=t>{h['hour']:02d}:00</td>"
                    f"<td class=c style='background:{COLOURS[h['band']]}'></td>"
                    f"<td><b>{escape(rule_hi)}</b><br><small>{escape(rule_en)}</small></td></tr>")
    if zr["band"] >= 1:
        headline = (f"{messages.window_hi(zr['start'], zr['end'])}: " +
                    ("भारी काम बंद" if zr["band"] >= 4 else f"हर घंटे {b['work']} मिनट काम, {b['rest']} मिनट छाँव में आराम"))
    else:
        headline = "आज गर्मी का खतरा कम है। पानी पीते रहें।"
    return f"""<!doctype html><html lang="hi"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>गर्मी सूचना — {escape(zone['name_hi'])}</title>
<style>
 @page {{ size: A4; margin: 12mm; }}
 body {{ font-family: 'Noto Sans Devanagari','Mangal',system-ui,sans-serif; color:#111; background:#fff; margin:0 auto; max-width:780px; padding:16px; }}
 h1 {{ font-size:44px; margin:0; }} h2 {{ font-size:26px; margin:6px 0 14px; }}
 .band {{ color:#fff; background:{COLOURS[zr['band']]}; padding:14px 18px; border-radius:10px; font-size:26px; font-weight:700; }}
 table {{ width:100%; border-collapse:collapse; margin:16px 0; font-size:20px; }}
 td {{ border-bottom:1px solid #ccc; padding:6px; }} td.t {{ width:90px; font-weight:700; }} td.c {{ width:40px; }}
 .rules {{ font-size:22px; line-height:1.5; }} .sos {{ font-size:30px; font-weight:800; color:#c62828; }}
 small {{ color:#555; }} .foot {{ font-size:13px; color:#555; margin-top:12px; }}
 @media print {{ .noprint {{ display:none; }} }}
</style></head><body>
<button class=noprint onclick="print()">🖨️ Print</button>
<h1>🔥 गर्मी सूचना</h1>
<h2>{escape(zone['name_hi'])} · {escape(city['name_hi'])} · {escape(messages.date_label(snap['date'], 'hi'))}</h2>
<div class=band>{escape(b['label_hi'])} — {escape(headline)}</div>
<table>{''.join(rows)}</table>
<div class=rules>💧 हर 15–20 मिनट पानी पिएँ, प्यास न लगे तब भी।<br>
⛱️ आराम छाँव में करें। सिर ढककर रखें।<br>
👀 साथी पर नज़र रखें: चक्कर, उल्टी, भ्रम, बहुत तेज़ सिरदर्द = खतरा।</div>
<p class=sos>🚨 आपात स्थिति: 112</p>
<div class=foot>Heavy-work guidance from hourly WBGT (peak {zr['peak_wbgt']}°C), ACGIH TLV screening values. {escape(city['plan_name'])} tier: {zr['tier']}. Weather: Open-Meteo (CC-BY 4.0), modelled. Not medical advice.{' REPLAY / EXERCISE.' if snap.get('mode') == 'replay' else ''}</div>
</body></html>"""
