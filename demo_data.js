// SAMPLE DATA ONLY: made-up records that match the dashboard's SAMPLE-001 to SAMPLE-003, so the
// assistant has something to find in a demo. Load it into a local database (replaces old demo data):
//   mongosh mongodb://localhost:27017/grants demo_data.js
// Deadlines are set relative to today, the same way the dashboard draws them.
const day = n => { const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() + n); return d; };
const iso = n => day(n).toISOString().slice(0, 10);

for (const c of ["opportunities", "requests", "budgets", "deadlines", "compliance", "policies", "documents", "team"]) db[c].drop();

db.opportunities.insertMany([
  { opportunity_id: "NIH-R21-SAMPLE", agency: "NIH", mechanism: "R21 (exploratory/developmental)",
    required_items: ["Specific aims", "Research strategy", "Budget", "Budget justification", "Biosketch", "Letters of support (if collaborators)"],
    budget_rules: "Direct costs may not exceed $275,000 over the two-year period, and no more than $200,000 in any one year.",
    page_limits: { "Specific aims": 1, "Research strategy": 6 },
    formatting_rules: { allowed_fonts: ["Arial", "Georgia", "Helvetica", "Palatino Linotype"], min_font_size: 11,
                        min_margin_in: 0.5, source: "NIH format: Arial, Georgia, Helvetica or Palatino Linotype, 11 pt or larger, margins of at least 0.5 inch" } },
  { opportunity_id: "FOUNDATION-SAMPLE", agency: "Sample Dental Research Foundation", mechanism: "Pilot grant",
    required_items: ["Budget", "Biosketch", "Research plan"],
    budget_rules: "Total award up to $50,000 for one year. Indirect costs are not allowed." }
]);

db.requests.insertMany([
  { request_id: "SAMPLE-001", pi: "[PI name 1]", department: "Oral Health and Diagnostic Sciences",
    opportunity_id: "NIH-R21-SAMPLE", status: "Incomplete",
    items: [ { name: "Budget", done: false }, { name: "Budget justification", done: false },
             { name: "Biosketch", done: true }, { name: "Specific aims", done: false } ] },
  { request_id: "SAMPLE-002", pi: "[PI name 2]", department: "Reconstructive Sciences",
    opportunity_id: "FOUNDATION-SAMPLE", status: "In review by SPS",
    items: [ { name: "Budget", done: true }, { name: "Biosketch", done: true }, { name: "Research plan", done: true } ] },
  { request_id: "SAMPLE-003", pi: "[PI name 3]", department: "Craniofacial Sciences",
    opportunity_id: "NIH-R21-SAMPLE", status: "Incomplete",
    items: [ { name: "Budget", done: false }, { name: "Budget justification", done: false },
             { name: "Letters of support", done: false } ] }
]);

db.budgets.insertMany([
  { request_id: "SAMPLE-001", year: 1, personnel: 120000, equipment: 25000, supplies: 30000, travel: 5000, indirect_rate: 0.58 },
  { request_id: "SAMPLE-001", year: 2, personnel: 70000, equipment: 0, supplies: 20000, travel: 5000, indirect_rate: 0.58 },
  { request_id: "SAMPLE-002", year: 1, personnel: 30000, equipment: 0, supplies: 15000, travel: 2000, indirect_rate: 0 },
  { request_id: "SAMPLE-003", year: 1, personnel: 150000, equipment: 40000, supplies: 25000, travel: 6000, indirect_rate: 0.58 },
  { request_id: "SAMPLE-003", year: 2, personnel: 60000, equipment: 0, supplies: 10000, travel: 4000, indirect_rate: 0.58 }
]);

db.deadlines.insertMany([
  { request_id: "SAMPLE-001", type: "Forms and budget", due: iso(1), what: "Budget due to SPS" },
  { request_id: "SAMPLE-001", type: "Collaborator input", due: iso(3), what: "External collaborator input due" },
  { request_id: "SAMPLE-001", type: "Compliance (IRB/IACUC)", due: iso(4), what: "IRB paperwork due" },
  { request_id: "SAMPLE-002", type: "SPS review", due: iso(6), what: "SPS internal review" },
  { request_id: "SAMPLE-002", type: "Submission", due: iso(9), what: "Submission deadline" },
  { request_id: "SAMPLE-003", type: "Compliance (IRB/IACUC)", due: iso(12), what: "IACUC protocol due" },
  { request_id: "SAMPLE-003", type: "Forms and budget", due: iso(16), what: "Forms due to SPS" },
  { request_id: "SAMPLE-001", type: "Submission", due: iso(21), what: "Submission deadline" }
]);

db.compliance.insertMany([
  { request_id: "SAMPLE-001", kind: "Human subjects (IRB)", document: "IRB application", version: "v3", whose_turn: "PI",
    last_note: "SPS: the consent form needs revised language." },
  { request_id: "SAMPLE-001", kind: "Animal use (IACUC)", document: "IACUC protocol", version: "v1", whose_turn: "External collaborator",
    last_note: "PI: draft sent to the outside contributor." },
  { request_id: "SAMPLE-002", kind: "Subaward", document: "Subaward agreement", version: "v2", whose_turn: "SPS",
    last_note: "External institution: signed copy attached." }
]);

db.policies.insertMany([
  { topic: "IRB", text: "Studies involving human subjects need IRB approval or an exemption before the award is accepted. Submit the IRB application at least 4 weeks before the grant deadline." },
  { topic: "IACUC", text: "Work with vertebrate animals needs an approved IACUC protocol. The protocol number must be listed in the application." },
  { topic: "Internal deadline", text: "Final application documents are due to SPS 5 business days before the sponsor deadline." },
  { topic: "Indirect costs", text: "The negotiated facilities and administrative (indirect) rate is 58% of modified total direct costs (sample rate)." }
]);

// A GOOD and a BAD sample application. Their text comes from samples/good_grant.docx and
// samples/bad_grant.docx, loaded with:  python judge/judge.py add-document samples/good_grant.docx SAMPLE-004
//                                       python judge/judge.py add-document samples/bad_grant.docx SAMPLE-005
// SAMPLE-004 is complete and within the limits. SAMPLE-005 has missing sections, leftover
// placeholders, a budget over both limits, unexplained costs, and no IRB application yet.
db.requests.insertMany([
  { request_id: "SAMPLE-004", pi: "[PI name 4]", department: "Pediatric Dentistry",
    opportunity_id: "NIH-R21-SAMPLE", status: "Ready for SPS review",
    items: [ { name: "Specific aims", done: true }, { name: "Research strategy", done: true },
             { name: "Budget", done: true }, { name: "Budget justification", done: true },
             { name: "Biosketch", done: true }, { name: "Letters of support", done: true } ] },
  { request_id: "SAMPLE-005", pi: "[PI name 5]", department: "Reconstructive Sciences",
    opportunity_id: "NIH-R21-SAMPLE", status: "Incomplete",
    items: [ { name: "Specific aims", done: true }, { name: "Research strategy", done: false },
             { name: "Budget", done: true }, { name: "Budget justification", done: true },
             { name: "Biosketch", done: false } ] }
]);
db.budgets.insertMany([
  { request_id: "SAMPLE-004", year: 1, personnel: 110000, equipment: 15000, supplies: 20000, travel: 5000, indirect_rate: 0.58 },
  { request_id: "SAMPLE-004", year: 2, personnel: 80000, equipment: 0, supplies: 25000, travel: 5000, indirect_rate: 0.58 },
  { request_id: "SAMPLE-005", year: 1, personnel: 160000, equipment: 45000, supplies: 20000, travel: 5000, indirect_rate: 0.58 },
  { request_id: "SAMPLE-005", year: 2, personnel: 70000, equipment: 0, supplies: 15000, travel: 5000, indirect_rate: 0.58 }
]);
db.deadlines.insertMany([
  { request_id: "SAMPLE-004", type: "SPS review", due: iso(5), what: "SPS internal review" },
  { request_id: "SAMPLE-004", type: "Submission", due: iso(14), what: "Submission deadline" },
  { request_id: "SAMPLE-005", type: "Compliance (IRB/IACUC)", due: iso(2), what: "IACUC protocol due" },
  { request_id: "SAMPLE-005", type: "Submission", due: iso(10), what: "Submission deadline" }
]);
db.compliance.insertMany([
  { request_id: "SAMPLE-004", kind: "Human subjects (IRB)", document: "IRB application", version: "v2",
    whose_turn: "Nobody: approved", last_note: "IRB: approved, protocol #SAMPLE-IRB-2041." },
  { request_id: "SAMPLE-005", kind: "Animal use (IACUC)", document: "IACUC protocol", version: "none",
    whose_turn: "PI", last_note: "Not started. The study uses an animal facility but no protocol has been submitted." }
]);

// Grant teams (people come from people.json). The assistant adds more through the chat.
const member = (request_id, person_id, name, role, title, department, institution, email, invited_by) =>
  ({ request_id, person_id, name, role, title, department, institution, email, invited_by: invited_by || "", added_at: new Date() });
db.team.insertMany([
  member("SAMPLE-001", "P001", "Dr. Maria Alvarez", "PI", "Associate Professor", "Oral Health and Diagnostic Sciences", "UConn Health", "malvarez@example.org"),
  member("SAMPLE-001", "P005", "Dr. Elena Rossi", "Co-Investigator", "Biostatistician", "Biostatistics Center", "UConn Health", "erossi@example.org", "Dr. Maria Alvarez"),
  member("SAMPLE-001", "P008", "Dr. Aisha Rahman", "Collaborator", "Professor of Public Health", "Epidemiology", "Partner University (external)", "arahman@example.org", "Dr. Maria Alvarez"),
  member("SAMPLE-004", "P002", "Dr. James Chen", "PI", "Professor", "Pediatric Dentistry", "UConn Health", "jchen@example.org"),
  member("SAMPLE-004", "P007", "Dr. Hannah Lee", "Co-Investigator", "Postdoctoral Fellow", "Pediatric Dentistry", "UConn Health", "hlee@example.org", "Dr. James Chen"),
  member("SAMPLE-005", "P003", "Dr. Priya Natarajan", "PI", "Assistant Professor", "Reconstructive Sciences", "UConn Health", "pnatarajan@example.org")
]);

// A sample draft application for SAMPLE-001, one record per section so the assistant can read it
// piece by piece. It has deliberate problems to find: the budget justification skips the $25,000
// of equipment in the budget, the Specific Aims run long, and there's a "TBD" left in.
db.documents.insertMany([
  { request_id: "SAMPLE-001", document: "Draft application v2", section: "Title", order: 1,
    text: "Salivary Biomarkers for Early Detection of Periodontal Disease Progression in Adults with Type 2 Diabetes" },
  { request_id: "SAMPLE-001", document: "Draft application v2", section: "Specific Aims", order: 2, pages: 1.4,
    text: "Periodontal disease affects nearly half of US adults and progresses faster in people with type 2 diabetes. " +
          "Clinicians currently detect progression only after irreversible attachment loss. Salivary biomarkers could " +
          "flag active disease earlier, but no panel has been validated in diabetic patients. " +
          "Aim 1: Identify salivary proteins that differ between stable and progressing periodontitis in 120 adults with " +
          "type 2 diabetes, sampled every 3 months for 12 months. " +
          "Aim 2: Build and internally validate a biomarker panel that predicts progression at the next visit. " +
          "Expected outcome: a validated panel ready for a larger R01 multi-site study." },
  { request_id: "SAMPLE-001", document: "Draft application v2", section: "Research Strategy", order: 3, pages: 5,
    text: "Significance: Earlier detection would let dentists intervene before permanent bone loss. " +
          "Innovation: First longitudinal salivary panel focused on diabetic patients. " +
          "Approach: Participants are recruited from the UConn Health dental clinics. Saliva is collected at each visit " +
          "and analysed by mass spectrometry. Human subjects: participants give written consent; IRB approval is pending. " +
          "Statistical analysis: mixed-effects models; sample size justified by a power analysis (TBD)." },
  { request_id: "SAMPLE-001", document: "Draft application v2", section: "Budget Justification", order: 4,
    text: "Personnel: PI (2.4 months/year), one postdoctoral fellow (100%), and a research coordinator (50%): " +
          "$120,000 in year 1 and $70,000 in year 2. Supplies: saliva collection kits and mass spectrometry " +
          "consumables, $30,000 in year 1 and $20,000 in year 2. Travel: one scientific meeting per year, $5,000 per year." },
  { request_id: "SAMPLE-001", document: "Biosketch", section: "Biosketch", order: 5,
    text: "[PI name 1], DMD, PhD. Associate Professor, Oral Health and Diagnostic Sciences. Ten years of clinical " +
          "periodontal research; five related publications." }
]);

print("Loaded demo data: " + ["opportunities", "requests", "budgets", "deadlines", "compliance", "policies", "documents", "team"]
  .map(c => c + "=" + db[c].countDocuments()).join(", "));
