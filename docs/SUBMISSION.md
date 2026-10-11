# Submission kit: Environmental Hacks (Heat and Water track)

We are submitting the **Build It** version: no AWS account (the app is hosted on Render and Vercel), using AWS open source (Strands Agents for the AI agents, AWS SAM for the deployment template).

## Checklist (one submission per team, before the deadline)

- [ ] **Public GitHub repo.** History falls within the event dates.
- [ ] **Live link.** The Vercel URL (frontend), backed by the app on Render. Before submitting, open it, run the 28 May 2024 replay from the Dashboard and check the bot answers. Fallback: the read-only GitHub Pages snapshot from `python scripts/export_static.py`.
- [ ] **Demo video, 3 minutes or less,** on YouTube (public or unlisted). Check that it plays in a signed-out browser. AWS has to be visible in the video, not only in the writeup: show the Strands agent working and the SAM template.
- [ ] **Short writeup:** problem, what we built, where AWS fits and the AI tools used (draft below).
- [ ] **Builder Center blog,** linked in the submission. The top 5 blogs win a prize (outline below).
- [ ] **Student status verified** on AWS Builder Center for every team member.
- [ ] **Confirm the submission cutoff time** on Discord.

## Writeup (draft)

**Problem.**
- NDMA counts more than 250 Heat Action Plans in India. Ahmedabad has had one since 2013 and Delhi since 2024.
- Reviews of these plans keep finding the same gaps:
  - warnings for the whole city based on temperature alone, with no humidity;
  - vulnerable groups not identified;
  - no way to check whether the actions were carried out. A 2026 audit of Delhi's plan could not find the plan text on any government portal.
- Outdoor workers find out the heat was dangerous after they have worked through it.

**What we built.** Taap runs a city's plan automatically, every hour:
1. It computes humidity-aware **WBGT** for each zone with the Liljegren model and turns it into **work and rest schedules** for heavy labour (ACGIH limits). New workers get stricter limits.
2. Workers get alerts in **Hindi or English on Telegram**, and a Hindi-first web page that reads the advice aloud and shares it on WhatsApp.
3. Each plan action goes to the responsible department. Officers mark it done in Telegram or on the Officers page, and the **public ledger** shows planned vs done for every department, with names and times.
4. Workers report problems (no water, no shade, work not stopped, someone unwell) and each report reaches the right officer.
5. **Saathi**, a voice assistant for workers in Hindi or English, answers questions about today's risk, nearby water and shade, and safe working hours, and files reports. Emergencies (fainting, vomiting, confusion) skip the AI and get fixed first aid steps and the 112 number.
6. A **shift planner** for contractors fits the hours of heavy work a site needs into the safe part of the day.
7. A **CAP 1.2 feed** (the format India's SACHET system uses) and **printable Hindi posters** reach people without smartphones.
8. A **replay of the May to June 2024 Delhi heatwave** shows what the plan would have triggered, day by day.
9. Any city is a config file; 7 cities are included. A new city can start from its plan PDF: an agent drafts the config, a validator checks it, and a person approves it.

**Where AWS fits.**
- **Strands Agents (AWS open source)** powers both AI features: the officer assistant and Saathi are Strands agents with tools that read live data, and the plan PDF importer is a Strands agent too. In the demo they run on Groq (`openai/gpt-oss-120b`).
- **AWS SAM (AWS open source):** `template.yaml` deploys the same code to EventBridge Scheduler, Lambda, DynamoDB, Amazon Polly (Hindi voice notes) and Amazon Bedrock. This path is ready but was not deployed for the demo.

**AI tools used.** Strands Agents SDK with Groq (`openai/gpt-oss-120b`) for the officer assistant, Saathi and the plan PDF importer. Claude Code was used as a coding assistant during development.

**Design choices.**
- Alerts and tasks come from fixed plan rules. The AI only answers questions and files reports, using tools over live data.
- Every threshold and action carries its source.
- Values are labelled as modelled, not measured.
- Zones match the forecast resolution (about 10 km) instead of pretending to be wards.
- Alert fatigue is limited: workers are alerted once per band per day, from band 2, never at night.

## 3-minute video script

Setup: the app running with `python scripts/run_local.py`. One Telegram account on the phone is the worker (sent `/start` and picked हिंदी, Delhi and Narela before recording). The officer uses the Officers page in the browser. The admin token is already saved on the Dashboard (gear icon).

| Time | On screen | Say |
|---|---|---|
| 0:00 to 0:20 | Home page; the 49.9 °C figure for Delhi on 28 May 2024 | "India has more than 250 Heat Action Plans. In May 2024 Delhi hit 49.9 °C. Can anyone tell you whether the plan actually ran?" |
| 0:20 to 0:45 | Dashboard, switch to Replay, pick 28 May 2024, click Run replay. Zones turn orange, band 4 | "Taap runs the plan. Every hour it computes heat stress the way the body feels it, from temperature, humidity, sun and wind, for every zone." |
| 0:45 to 1:10 | Phone: the Hindi heat alert arrives in Telegram. Then the Workers page: tap सुनें so it reads the advice aloud | "A construction worker in Narela gets this in Hindi: exactly when to stop heavy work. The page reads it aloud and shares it on WhatsApp." |
| 1:10 to 1:35 | Officers page: enter the officer code, click Mark done on Water points. Open the Ledger: the action shows with the name and time | "Each action in the plan goes to the department that owns it. When they act, it goes on the public ledger. Plan versus reality." |
| 1:35 to 1:55 | Phone: tap "No drinking water". Officers page: the report appears, click Resolve | "Workers can report back, and the report reaches the right department." |
| 1:55 to 2:15 | Dashboard: ask the assistant "Which zones still have open actions?" | "Officers can ask the plan assistant. It is a Strands agent that answers only from live data." |
| 2:15 to 2:40 | `src/handlers/agent.py` (the Strands `Agent` and its `@tool` functions), the terminal line "Assistant: Strands Agents + Groq", then `template.yaml` | "The agents are built with Strands Agents, AWS open source. The same code deploys to AWS with SAM: EventBridge, Lambda, DynamoDB, Polly and Bedrock." |
| 2:40 to 3:00 | Saathi: ask in Hindi when to work today. Then the live Delhi view: plan tier green, worker band high | "Saathi answers workers by voice, in Hindi. Even today, a humid October afternoon is unsafe for heavy work, while the temperature-only plan says green. 250 plans exist. Let's make them run." |

## Blog outline (AWS Builder Center)

**Title:** *Why we measured WBGT, not temperature, to run Delhi's Heat Action Plan*

1. **The gap.** Plans exist, but they are temperature-only and can't be checked. Cite CPR 2023, the 2026 audit of Delhi's plan and the Greenpeace and IMD worker pilot.
2. **The physics.** Why WBGT; the Liljegren model; why we avoided "simplified WBGT"; the ACGIH bands. Include a chart of the October example.
3. **The build.** The plan engine; Strands agents with tools that read live state (officer assistant, Saathi, PDF importer) on Groq; SQLite and Telegram long polling locally; the SAM template for AWS.
4. **What broke:**
   - Open-Meteo rate-limits shared IPs, so we cache the history.
   - Telegram button data is limited to 64 bytes, so report keys had to be short.
   - Model answers came back with markdown and citation marks that Telegram shows as raw text, so we clean them.
   - Indian SMS needs carrier (DLT) registration, so we used Telegram.
   - On Windows, a date format the code used doesn't exist, which left pages empty until we fixed it.
5. **What the 2024 replay showed:** the backtest numbers from the dashboard.
6. **Limits and next steps:**
   - WhatsApp and missed-call or voice channels for feature phones;
   - ward-level vulnerability layers;
   - correcting forecasts against IMD stations;
   - deploying the SAM stack with Bedrock and Polly voice notes.
