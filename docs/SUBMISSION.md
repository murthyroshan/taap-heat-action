# Submission kit: Environmental Hacks (Heat and Water track)

## Checklist (one submission per team, before the deadline)

- [ ] **Public GitHub repo.** The commit history must fall within the event dates.
- [ ] **Live link.** Either `DashboardUrl` from the AWS stack, or the GitHub Pages snapshot (`python scripts/export_static.py`, push `docs/`, enable Pages on master /docs). Run a live plan run **and** the 28 May 2024 replay first, so judges see real data.
- [ ] **Demo video, 3 minutes or less,** on YouTube (public or unlisted). Open the link in a signed-out browser to check it plays. **AWS must be visible in the video**; mentioning it only in the writeup does not count.
- [ ] **Short writeup:** problem, what you built, where AWS fits, and the AI tools used (draft below).
- [ ] **Builder Center blog,** linked in the submission. The top 5 blogs win a prize (outline below).
- [ ] **Student status verified** on AWS Builder Center.

## Writeup (draft)

**Problem.**
- Delhi has had a Heat Action Plan since 2024, and Ahmedabad since 2013. NDMA counts 250+ such plans.
- Reviews of these plans keep finding the same gaps:
  - city-wide warnings based on temperature alone, with no humidity;
  - no vulnerable groups identified;
  - no way to check whether actions happened. A 2026 audit of Delhi's plan could not find the plan text on any government portal.
- Outdoor workers find out the heat was dangerous after they have worked through it.

**What we built.** Taap runs a city's plan automatically, every hour:
1. It computes humidity-aware **WBGT** for each zone, using the Liljegren model, and turns it into **work–rest schedules** for heavy labour (ACGIH limits).
2. It sends workers **Hindi voice notes and text** on Telegram.
3. It sends each triggered plan action to the responsible department's officer, who taps **Done**. The **public plan-vs-reality log** records who did what and when.
4. Workers report back (no water, no shade, work not stopped, someone unwell), and each report reaches the right officer.
5. A **CAP 1.2 feed** (SACHET's format) and **printable Hindi posters** for labour chowks reach people without smartphones.
6. A **backtest on the May–June 2024 Delhi heatwave** shows what the plan would have triggered.
7. Any city is a config file; Delhi and Ahmedabad are included. A new city can start from its plan PDF: Bedrock drafts the config, a validator checks it, and a person approves it.

**Where AWS fits.** The project runs two ways from the same code:
- **Build It (what the demo shows):** on one machine with no AWS account. The officer assistant and the plan-PDF reader are agents built with **Strands Agents** (AWS open source), running on Groq or a local model.
- **Ship It (ready to deploy):** `template.yaml` deploys the same code with **AWS SAM** (open source) to the services below.


| Service | Job |
|---|---|
| EventBridge Scheduler | Runs the hourly plan |
| Lambda + function URLs | Engine, Telegram webhook, dashboard/API, agent |
| DynamoDB | State and audit trail |
| Amazon Polly | Hindi voice |
| Amazon Bedrock + Strands Agents (open source) | Officer assistant with read-only tools over live state; drafting city configs from plan PDFs |
| AWS SAM (open source) | Infrastructure as code |
| CloudWatch | Logs |

**Design choices we can defend.**
- Alerts and tasks come from deterministic plan rules; the AI agent only answers questions about them.
- Thresholds and actions carry their source.
- Values are labelled as modelled.
- Zones match the forecast resolution instead of pretending to be wards.
- Alert-fatigue guards are built in.


## 3-minute video script

| Time | On screen | Say |
|---|---|---|
| 0:00–0:20 | Headlines from the May 2024 heatwave; IMD's 49.9 °C | "Delhi has a Heat Action Plan. In May 2024 it hit 49.9 °C. Can anyone tell you whether the plan ran?" |
| 0:20–0:45 | Dashboard → Replay → 28 May 2024 → Run | "Taap runs the plan. Every hour it computes heat stress the way a body feels it — temperature, humidity, sun and wind — for every zone." |
| 0:45–1:15 | Phone: Hindi voice note plays; text shows the work–rest schedule | "A construction worker in Narela gets this in Hindi, as a voice note: exactly when to stop heavy work." |
| 1:15–1:40 | Officer phone: action → tap ✅ Done → dashboard row turns Done | "The plan's actions go to named officers. When they act, the public log shows it. Plan versus reality." |
| 1:40–1:55 | Worker taps "No water" → officer gets it → Resolved | "Workers can report back. Reports reach the right department." |
| 1:55–2:15 | Ask the assistant: "Which zones still have open actions?" | "Officers can ask the plan assistant — a Strands agent on Amazon Bedrock that reads live state and doesn't make things up." |
| 2:15–2:40 | Architecture diagram; console: EventBridge schedule, Lambda logs, DynamoDB items, CAP XML | "All on AWS: EventBridge, Lambda, DynamoDB, Polly, Bedrock, deployed with SAM. A standard CAP feed lets it plug into India's SACHET alerts." |
| 2:40–3:00 | Live October view: plan tier green, worker band very high; switch city to Ahmedabad; upload a plan PDF and get a draft config; Hindi poster | "Even today, a humid October afternoon is unsafe for heavy work — the temperature-only plan says green. Any city is a config file. 250 plans exist. Let's make them run." |

## Blog outline (AWS Builder Center)

**Title:** *Why we measured WBGT, not temperature, to run Delhi's Heat Action Plan on AWS*

1. **The gap.** Plans exist, but they're temperature-only and can't be checked. Cite CPR 2023, the Leaflet audit (2026) and the Greenpeace/IMD worker pilot.
2. **The physics.** Why WBGT; the Liljegren model; why we avoided "simplified WBGT"; ACGIH bands. Include a chart of the October example.
3. **The architecture.** EventBridge → Lambda → DynamoDB → Polly/Telegram; Strands on Bedrock as a read-only assistant; SAM.
4. **What broke:**
   - Open-Meteo rate-limits shared IPs, so we cache and bundle data.
   - DynamoDB's 400 KB item limit, so we compress.
   - Indian SMS needs carrier (DLT) registration, so we used Telegram.
   - Bedrock access on new accounts.
5. **What the 2024 replay showed:** the backtest numbers from the dashboard.
6. **Honest limits and next steps:**
   - WhatsApp and missed-call/voice channels for feature phones;
   - ward-level vulnerability layers;
   - correcting forecasts against IMD stations;
   - parametric insurance triggers.
