"""Common Alerting Protocol (CAP 1.2) feed: the format India's SACHET system ingests."""
from __future__ import annotations

from xml.sax.saxutils import escape

from .plan import BANDS, load_city, zone_by_id

SEVERITY = ["Minor", "Minor", "Moderate", "Severe", "Extreme"]
ZONE_RADIUS_KM = 5


def build(snap: dict, mode: str, sender: str = "taap-heat-plan") -> str:
    city = load_city(snap["city"])
    status = "Exercise" if mode == "replay" else "Actual"
    sent = snap.get("generated_at", "")
    infos = []
    for zr in snap["zones"]:
        if not zr.get("available") or zr["band"] < 1:
            continue
        zone = zone_by_id(city, zr["zone"])
        b = BANDS[zr["band"]]
        instr = ("Stop heavy outdoor work" if zr["band"] >= 4
                 else f"Heavy work: {b['work']} min work / {b['rest']} min rest per hour")
        instr += f" between {zr['start']} and {zr['end']} IST. Drink water every 15-20 min. Emergency: 112."
        infos.append(f"""  <info>
    <language>en-IN</language>
    <category>Met</category>
    <category>Health</category>
    <event>Occupational heat stress</event>
    <urgency>Expected</urgency>
    <severity>{SEVERITY[zr['band']]}</severity>
    <certainty>Likely</certainty>
    <senderName>{escape(city['plan_name'])} (automated)</senderName>
    <headline>{escape(f"{b['label']} heat stress in {zone['name']} on {snap['date']}")}</headline>
    <description>{escape(f"Peak WBGT {zr['peak_wbgt']} C at {zr['peak_hour']:02d}:00 IST; Tmax {zr['tmax']} C; plan tier {zr['tier']}" + ("; hot night" if zr.get('hot_night') else ""))}</description>
    <instruction>{escape(instr)}</instruction>
    <parameter><valueName>WBGT_C</valueName><value>{zr['peak_wbgt']}</value></parameter>
    <parameter><valueName>PLAN_TIER</valueName><value>{zr['tier']}</value></parameter>
    <area>
      <areaDesc>{escape(zone['name'] + ', ' + city['name'])}</areaDesc>
      <circle>{zone['lat']},{zone['lon']} {ZONE_RADIUS_KM}</circle>
    </area>
  </info>""")
    body = "\n".join(infos)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<alert xmlns="urn:oasis:names:tc:emergency:cap:1.2">
  <identifier>{escape(f"taap-{snap['city']}-{mode}-{snap['date']}-" + "".join(ch for ch in sent if ch.isdigit())[:14])}</identifier>
  <sender>{escape(sender)}</sender>
  <sent>{escape(sent)}</sent>
  <status>{status}</status>
  <msgType>Alert</msgType>
  <scope>Public</scope>
{body}
</alert>
"""
