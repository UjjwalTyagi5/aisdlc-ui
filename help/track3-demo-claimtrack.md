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

1. **Pull legacy code** → *Project 2* → repository *Project 2*, branch `main` → Pull. The
   header shows the repository and commit when it is done (under a minute).
2. **Run Requirements agent**, then send the prompts below in order. The agent asks at most
   three questions a turn; these answers cover everything it requires, so it records the
   brief after the third message. If it asks for something not covered, use the fallback
   answers at the end.

**Prompt 1**
```
Let's capture the migration intent for ClaimTrack, Contoso Insurance's claims management system. The legacy code has been pulled for this project — start from what it shows.
```
*Expect:* it states what the code shows (5 components, Java 8/7, Node 8, Python 2.7, what
is end-of-life) and asks you to confirm, then asks why and what the target is.

**Prompt 2 — confirm, why, from → to**
```
Yes, that's correct — those five components are all of ClaimTrack. Today it runs on four on-premises VMs in our Dallas data centre (two Tomcat 8.5 app servers, one batch VM, one reporting VM) against a MySQL 5.6 database.

Why we're modernizing:
1. End of support — the settlement batch runs on Java 7 (unsupported since July 2022), the reports on Python 2.7 (unsupported since January 2020), and the agent portal on AngularJS 1.5 and Node 8, both end-of-life. Java 8 is out of premier support and MySQL 5.6 is end-of-life as well.
2. Security and audit — our 2026 internal audit and the last SOC 2 review flagged Log4j 1.x and libraries with known critical vulnerabilities. The audit committee wants them remediated before the next SOC 2 Type II window in March 2027.
3. Cost — the Dallas data-centre lease ends on 30 June 2027 and will not be renewed. Hosting these VMs costs about $310,000 a year.
4. Skills — we can no longer hire AngularJS or Java 7 developers, and two of the three engineers who know the settlement batch retire next year.

Target: Java 21 with Spring Boot 3.3 for the web API and the settlement batch (Spring Batch), React 18 with TypeScript for the agent portal, and Python 3.12 for the reporting — all on Azure Container Apps, with Azure Database for MySQL Flexible Server 8.0, deployed from Azure DevOps pipelines.
```

**Prompt 3 — scope, constraints, success criteria, stakeholders**
```
Scope — in: all five components (claimtrack-core, claimtrack-web including the adjuster screens and the /api/v1 claims REST API, claimtrack-batch, the agent portal and the reports), plus moving the MySQL database to Azure. Out: the PolicyHub policy administration system (we only consume its nightly extract), the external RiskLens fraud-scoring service, the bank's payment gateway, the data warehouse, and any new product features.

Constraints: cut over by 30 June 2027, one component at a time, with at most 2 hours of downtime per cutover in a Sunday 00:00–04:00 window. The /api/v1 claims API must not change — broker partners call it directly. The bank payment file (94-character fixed-width format) and the regulator's CR-4 quarterly return must stay byte-for-byte identical. Policyholder personal data must stay in US Azure regions. Budget cap: $450,000. From 1 February 2027 the legacy system is frozen except for P1 fixes.

Success criteria: identical payouts on a recorded set of 10,000 historical claims; identical bank payment files for three months of recorded nightly batch inputs; the Q4 2026 regulator return reproduced byte-for-byte; API p95 latency at or below 300 ms (about 800 ms today); zero critical or high vulnerabilities at go-live; at least 80% unit-test coverage on the claim rules; legacy VMs decommissioned by 30 September 2027.

Stakeholders: Priya Raman, Head of Claims Operations (business owner); Daniel Okafor, Enterprise Architect; Mei Chen, Security & Compliance lead; Tom Becker, IT Operations manager.

Please record the brief.
```
*Expect:* the brief is recorded, appears on the left as **Brief v1** and opens by itself.
Show: the from → to card, the scope and constraints as given (nothing added), **Word /
PDF** download, and the sign-off.

Optional follow-ups:
```
Which parts of the current system did you read from the code, and which came from me?
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

On the same project, after the code is pulled:

1. `hi` — it introduces Code Modernization and asks why and from what to what.
2. `We're modernizing ClaimTrack, Contoso Insurance's claims management system — the legacy code is already pulled for this project. Help me capture the migration intent.`
3. Prompt 2 above.
4. Prompt 3 above.
5. `Proceed to discovery and assessment. The target is Java 21 with Spring Boot 3, React 18 with TypeScript, and Python 3.12 on Azure.`
6. Open **Deliverables**: the Migration Intent Brief and the Discovery & Assessment report.
   Both also appear as versions on the two agent pages.

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
