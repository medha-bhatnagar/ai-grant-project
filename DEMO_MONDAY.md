# Monday demo: collaboration docket, CV uploads, and AI emails

What the professor asked for (email + Overview.pptx), and where it is in the app:

| He asked for | In the app |
| --- | --- |
| Scenario 1: "create the initial collaboration docket" from a list of investigators | Chat → **Controller** (Judge) → **DB agent** looks up affiliations → **Filler** fills the Docket panel |
| Drag and drop / upload per investigator, SPS tracks who submitted what | **Docket** tab: a drop zone per investigator, with document type and who uploaded it when |
| "Who has or has not submitted CVs?" | Chat answers from the docket: 3 of 5 |
| "Send emails to those who have not submitted CVs" | AI drafts the prefab email, asks first, then puts it in the **Outbox** (never really sent) |
| Scenario 2: annual research security training | Chat → **Compliance agent** checks `compliance.csv` |

Data: the professor's `Demo Plan` CSVs, copied into `demo/`. The CVs to drag in are in
`Downloads\OneDrive_2026-11-15\4939W_Team 44\Data_Samples\CVs`.

## Before the meeting (5 minutes)

1. Start it on your PC:
   ```
   powershell -ExecutionPolicy Bypass -File run_local.ps1
   ```
2. Open http://localhost:8080. On the **Staff** card, click **Continue to UConn sign-in**, then pick **Demo SPS Staff**.
3. Click the **Docket** tab. If an old docket is there, scroll down and click **Reset the demo**.
4. Open the CVs folder in File Explorer, next to the browser.

## The demo (about 5 minutes; each AI reply takes 15–40 seconds)

**1. Create the docket** (type in the chat):
> I plan to respond to RFA-HG-27-007 and there will be a team of investigators: George Liu, James Slaughter, Chih-Ming Tsai, C. Creech and Issac Thomasen. Can you create the initial collaboration docket for this application?

The left panel fills with the 5 investigators and their affiliations. The reply says "Consulted: db_agent".

**2. Upload CVs** (the drag-and-drop part): drag `Liu_CV.pdf` onto George Liu's drop zone,
`Slaughter_CV.pdf` onto James Slaughter's, and `Creech_CV.pdf` onto C.Creech's. Each shows a ✅ CV
submitted tag, the file link, and who uploaded it when.

**3. Ask:**
> Who has or has not submitted their CV?

→ 3 of 5 submitted. Missing: Chih-Ming Tsai and Issac Thomasen.

**4. Ask:**
> Can you send emails to those who have not submitted CVs?

→ It shows the 2 drafts and asks "Send these 2 emails?". Then type:
> Yes, send them

→ Both emails appear in the **Outbox** at the bottom of the Docket tab.

**5. Ask (Scenario 2):**
> Has everyone on the docket completed their annual research security training?

→ "Consulted: compliance_agent": Slaughter and Tsai done, Liu not done, Creech and Thomasen no record.

## Things to say

- The **Judge is the Controller**. Each specialist is one JSON file in `agents/llms/`
  (`db_agent.json`, `compliance_agent.json`, …). Adding an agent means adding a file.
- Agents can only use the tools their file lists (`"tools": [...]`). The email tool only runs after
  the user says yes.
- The emails go to a demo outbox on purpose: the sample files contain real UConn email addresses.
- Only signed-in users can see the docket or use the chat (UConn sign-in later, demo sign-in now).

## The professor's data, as it is

- `affiliation_sample1.csv` reuses the emails of `affiliation_sample.csv` (Liu has Dong Shin's
  address). The AI notices and says so.
- `compliance.csv` lists the older names (Shin, Maye, Rowe). Training is matched by email, so
  Liu = no and Slaughter and Tsai = yes, while Creech and Thomasen have no record.
- His slide uses David Rowe, Peter Maye and Dong-Guk Shin. That works too: ask the same question
  with those names.
