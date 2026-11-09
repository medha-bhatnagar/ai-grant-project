"""Creates the two sample grant files (sample data only, made up):
    good_grant.docx  SAMPLE-004: complete, within budget limits, every cost explained,
                     NIH formatting (Arial 11 pt, 1 inch margins)
    bad_grant.docx   SAMPLE-005: missing sections, placeholders, costs not explained,
                     wrong formatting (Times New Roman 10 pt, 0.3 inch margins)
Run:  python make_samples.py     (needs python-docx)"""
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt

HERE = Path(__file__).resolve().parent


def build(file_name, title, sections, font="Arial", size=11, margin=1.0):
    doc = Document()
    doc.styles["Normal"].font.name = font
    doc.styles["Normal"].font.size = Pt(size)
    for s in doc.sections:
        s.top_margin = s.bottom_margin = s.left_margin = s.right_margin = Inches(margin)
    doc.add_heading(title, 0)
    for heading, paragraphs in sections:
        doc.add_heading(heading, 1)
        for text in paragraphs:
            doc.add_paragraph(text)
    doc.save(HERE / file_name)
    print("wrote", file_name)


build("good_grant.docx", "Fluoride Varnish Adherence Among Children in Community Dental Clinics", [
    ("Specific Aims", [
        "Early childhood caries remain the most common chronic disease of childhood, and fluoride varnish "
        "applied every six months prevents them, yet fewer than half of eligible children in community "
        "clinics receive both yearly applications.",
        "Aim 1: Measure varnish adherence for 400 children aged 1 to 5 across four Connecticut community "
        "dental clinics over 18 months.",
        "Aim 2: Test whether automated text-message reminders to parents raise the share of children who "
        "receive both yearly applications from 45% to at least 65%.",
        "Expected outcome: a low-cost reminder approach ready for a larger multi-state R01 trial."]),
    ("Research Strategy", [
        "Significance: Missed varnish visits are a leading, preventable cause of caries in low-income children.",
        "Innovation: First randomized test of automated reminders tied directly to clinic scheduling systems.",
        "Approach: Clinics are randomized to reminders or usual care. Adherence is measured from visit "
        "records. Analysis uses mixed-effects logistic regression; 400 children give 80% power to detect a "
        "20-point difference at alpha 0.05."]),
    ("Budget Justification", [
        "Personnel: PI (1.2 months/year), a project coordinator (100%) and a data analyst (25%): $110,000 "
        "in year 1 and $80,000 in year 2.",
        "Equipment: a secure messaging server and clinic tablets, $15,000 in year 1.",
        "Supplies: varnish kits and printed parent materials, $20,000 in year 1 and $25,000 in year 2.",
        "Travel: site visits to the four clinics, $5,000 per year."]),
    ("Biosketch", [
        "[PI name 4], DDS, MPH. Associate Professor of Pediatric Dentistry. Twelve years of community "
        "oral health research; eight related publications."]),
    ("Letters of Support", [
        "Signed letters from the directors of all four participating community dental clinics."]),
])

build("bad_grant.docx", "Novel Antimicrobial Coatings for Dental Implants", [
    ("Specific Aims", [
        "Peri-implantitis causes many dental implant failures. Aim 1: Test a new antimicrobial implant "
        "coating in the lab. Aim 2: TBD. We will use [insert collaborator name]'s animal facility."]),
    ("Budget Justification", [
        "Personnel: PI (2 months/year), two graduate students and a postdoctoral fellow: $160,000 in "
        "year 1 and $70,000 in year 2."]),
    ("Biosketch", [
        "TODO: add biosketch."]),
], font="Times New Roman", size=10, margin=0.3)
