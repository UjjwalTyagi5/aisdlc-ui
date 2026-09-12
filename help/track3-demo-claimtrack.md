# Track 3 demo — modernizing ClaimTrack (Contoso Insurance)

A realistic Code Modernization scenario for showing Track 3's first two agents —
Requirements (migration intent) and Discovery & Assessment — end to end.

**The legacy system** is in Azure DevOps: `srk02804 / Project 2 / Project 2` (branch
`main`), local copy at `C:\Users\srk02\Downloads\Frontend\claimtrack-legacy`. Keep this
script OUT of that repository — the agents read the repository, and the answers must not be
sitting in it.

## The story (for the audience)

Contoso Insurance's claims system, ClaimTrack, was built in 2014–2016 and has not been
modernized since: a Java 8 claim-rules library and Spring MVC 4 / JSP web app on Tomcat, a
nightly settlement batch still on **Java 7**, an **AngularJS 1.5** broker portal built on
**Node 8**, and **Python 2.7** regulatory reports — three runtimes past end of support,
Log4j 1.x in production, and a data-centre lease that ends in June 2027. The target is
**Java 21 + Spring Boot 3, React 18 + TypeScript, Python 3.12 on Azure**.

Why this scenario and not, say, COBOL → Python: Discovery reads Maven/Gradle, npm, pip and
.NET projects and has a dated end-of-life table for Java, Node, Python and .NET, so every
part of this system is inventoried, flagged and scored. A COBOL estate has no manifests or
runtime versions for it to read, and its report would be thin.

## What the agents will produce (dry run, 2026-09-11)

| | |
|---|---|
| Modules | 5 — claimtrack-core, claimtrack-web, claimtrack-batch, claimtrack-agent-portal, claimtrack-reports (1,826 lines: Java, JavaScript, Python, SQL) |
| End of life | Java 7 (ended 2022-07-31), Node.js 8 (2019-12-31), Python 2.7 (2020-01-01); Java 8 = legacy |
| Deprecated | 9 — Log4j 1.x (web, batch), commons-httpclient 3, request, moment, node-sass, gulp-util, pycrypto, nose |
| Vulnerabilities | ~88 known CVEs, ~12 critical — Log4j 1.x (CVE-2019-17571), Spring4Shell (CVE-2022-22965), commons-fileupload, PyYAML, pycrypto, Quartz. Counts move with Trivy's database. |
| Tiers | 1 **mechanical** (claimtrack-core: Java 8 → 21 by upgrade tooling), 4 **LLM-assisted**, **0 manual** |
| Graph | claimtrack-web → claimtrack-core, claimtrack-batch → claimtrack-core |

Talking points: the platform read the real code, not a questionnaire; every risk point is
attributed; no module needs a manual redesign; the brief and the assessment are frozen
versions that download as Word/PDF and are signed off by the BA; the code was pulled with
the project's Azure DevOps connection only because the Business Unit holds the grant and the
project wired it to the Requirements stage.

## Before the demo

1. Docker Desktop (Redis), backend on :8004, frontend on :3000.
2. A Track 3 (Code Modernization) project — e.g. **ClaimTrack Modernization** in the
   PAYMENTS Business Unit — approved by the Business Unit admin. In its settings, wire
   **Azure DevOps** to the **Requirements (migration intent)** stage (read access is
   enough). Wire it to **Discovery & Assessment** too if you want to pull from that page.
3. Model picker: **Azure gpt-5-mini** (the Anthropic key is at its spend limit).
4. Sign-off: nobody can approve a version they produced — have a second person (BA or
   Project Admin) ready to click Approve, or show the button and explain the rule.

## Flow A — the two agent pages

### 1. Requirements (migration intent)

The agent does NOT get told the target stack — it recommends one from the code and the
reasons you give, you confirm, and it records a designed brief.

1. **Pull legacy code** → *Project 2* → repository *Project 2*, branch `main` → Pull (or,
   in the chat: "Pull our legacy code — it's the Project 2 repository in Azure DevOps").
2. **Run Requirements agent** → **New chat**, and paste:

**Prompt 1 — the situation, in your own words (no target stack)**
```
Hi! I'm the business analyst on Contoso Insurance's claims modernization programme. The code you can see is ClaimTrack, our claims management system: the claim rules library, the web app our adjusters use along with the REST API our brokers call, the nightly batch that settles approved claims with the bank, the broker portal, and the monthly and regulator reports. It was built around 2015 and has barely been touched since, and it all runs on our own servers in the Dallas data centre with a MySQL 5.6 database. We need to modernize it for a few reasons. Much of the stack is out of support. Our last internal audit and SOC 2 review flagged Log4j 1.x and several libraries with critical vulnerabilities, which we have to fix before our next SOC 2 audit in March 2027. The Dallas data centre lease ends on 30 June 2027 and we're not renewing it; those servers cost us around $310,000 a year. And honestly, we can't find AngularJS or Java 7 developers any more, and two of the three people who understand the batch retire next year. We're an Azure shop and we already use Azure DevOps for our pipelines, but we'd like your recommendation on what the target stack should be. All five components and the database move are in scope. PolicyHub (we just consume its nightly extract), the external RiskLens fraud service, the bank's payment gateway, the data warehouse and any new features are not. We have to be off the old servers by the end of June 2027, ideally moving one component at a time with no more than two hours of downtime per cutover on a Sunday night, the budget is $450,000, and the legacy system is frozen from 1 February 2027 except for P1 fixes. A few things can't change at all: the /api/v1 claims API our broker partners call, the bank payment file format, and the regulator's CR-4 quarterly return. Policyholder data also has to stay in US Azure regions. We'll call it a success if payouts come out identical on a recorded set of 10,000 past claims, the bank files and the Q4 2026 regulator return match the old system exactly, the API responds within 300 ms at p95 (it's about 800 ms today), there are no critical or high vulnerabilities at go-live, and the old servers are switched off by the end of September 2027. Priya Raman, our Head of Claims Operations, is the business owner. What would you recommend?
```
*Expect (about a minute):* it confirms what the code shows (five modules; Java 7, Node 8
and Python 2.7 past end of support; Log4j 1.x), then recommends a target per part of the
system with the reason, the concrete change per module, the trade-offs and the alternatives
it rejected — and asks whether it looks right. It may ask two or three short questions.

**Prompt 2 — agree (answer its questions in the same message if it asked any)**
```
Yes, that's the whole system and those four are out of scope. Go PaaS-first, React with TypeScript for the portal, and Azure Database for MySQL Flexible Server in US regions is fine. Looks good, please record the brief.
```
*Expect:* a short "recorded" reply, and **Brief vN** opens on the left as a designed
document in the PwC palette: a title band running white into orange with the goal and key
facts (deadline, budget, scope, end-of-life count, "Recommended"), then numbered sections —
the change at a glance (today in grey, end of life marked red → the target in orange, and
the kind of change, whose orange deepens from Upgrade to Rewrite), why, the recommended
stack with its reasons and rejected alternatives, scope, what changes in each module (with
the change mix and effort), trade-offs, a timeline, constraints and the success measures.

Show: **Word (.docx)** and **PDF** — the same design, ready to send — and the sign-off.

Optional follow-ups:
```
Why did you recommend Java 21 rather than rewriting in .NET?
```
```
Where does the web app call the external fraud service? Show me the code.
```

### 2. Discovery & Assessment

The page shows the same pulled code in its header — no second pull. **Run Discovery &
Assessment**:

**Prompt 1**
```
Assess the legacy ClaimTrack code that is already pulled for this project. The target is Java 21 with Spring Boot 3, React 18 with TypeScript, and Python 3.12 on Azure.
```
*Expect (about a minute):* it confirms the repository and commit, runs the assessment
(Trivy included) and replies with the report and its assessment notes; **Assessment v1**
appears on the left. Open it: summary strip, the module table (filter by tier), a module's
risk factors, the dependency graph, and the Flags tab.

Follow-ups that show depth:
```
Why is claimtrack-core mechanical while claimtrack-web is LLM-assisted?
```
```
Which dependencies must be replaced rather than upgraded, and why?
```
```
Show me the details for claimtrack-batch.
```
```
Export the full assessment as a Word document.
```

## Flow B — the Orchestrator (Project Admin)

The Orchestrator is self-contained: everything happens in its chat, including pulling the
code, and what it produces stays in that conversation's **Deliverables** (it is not added to
the agent pages' history, and code pulled here is this conversation's own copy).

1. `hi` — it introduces Code Modernization and suggests pulling the legacy code first.
2. `Can you pull our legacy code? It's the Project 2 repository in our Azure DevOps.` — the
   Requirements agent lists what the connection wired to its stage can see (pick *Project 2*
   if it asks), pulls it read-only and summarises what the code shows.
3. Prompt 1 above — it recommends the target; then Prompt 2 — it records the brief.
4. `Proceed to discovery and assessment. The target is Java 21 with Spring Boot 3, React 18 with TypeScript, and Python 3.12 on Azure.`
   — Discovery assesses the code already pulled in this conversation; no second pull.
5. Open **Deliverables**: the Migration Intent Brief and the Discovery & Assessment report.

## Fallback answers (if the agent asks for more)

- **Assumptions:** the Azure landing zone and networking are provided by the Cloud
  Platform team; broker partners get 60 days' notice of any cutover; the PolicyHub nightly
  extract continues unchanged.
- **Risks:** the settlement batch and the web app have no automated tests; the fraud
  vendor's API is undocumented beyond the one endpoint we call; the regulator validates the
  CR-4 file format strictly.
- **Open questions:** whether the adjuster JSP screens move into the React portal or stay a
  separate app — Architecture decides in Design.
- **Deadline detail:** data-centre exit 30 June 2027; SOC 2 Type II window March 2027.
- **Budget detail:** $450,000 cap, including Azure run costs for the first year.
