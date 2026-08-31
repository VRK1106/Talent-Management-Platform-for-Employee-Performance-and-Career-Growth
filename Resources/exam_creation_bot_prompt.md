# Exam Creation Chatbot — Consolidated Flow Spec

## Purpose
Guide an instructor through creating an exam via conversational chatbot, from source
material to a scheduled, secured, gradable exam — collecting structured data at each
step and validating before allowing progression.

---

## Step 1 — Title & Instructions
- Exam title
- General instructions (free text, shown to students before they start)
- Exam category/type tag (e.g. Midterm, Practice, Mock Interview, Quiz) — used later for
  templates and analytics
- Language (if platform is multilingual)

**Validation:** title required, instructions non-empty.

---

## Step 2 — Source Selection & Weighting
- Select one or more documents from the database
- **Per-file weightage** (not equal split): instructor sets either
  - a percentage/ratio of total questions per file, or
  - lets the AI auto-weight by detected content depth (section count, page length)
- **Topic/section tagging within each file** (auto-extracted): instructor can include/exclude
  specific sub-topics rather than treating the whole file as one unit
- Toggle: **Exclude questions used in [previous exam(s)]** — select prior exams to check against
- Difficulty distribution: slider or explicit ratio (Easy / Medium / Hard)
- Optional: Bloom's-taxonomy tagging (Recall / Application / Analysis / Evaluation) —
  relevant if positioning as interview-prep rather than rote-recall testing

**Validation:** at least one document selected; weights sum to 100%.

---

## Step 3 — Structure & Sections
Replaces flat "number of questions / mark per question" with section-based structure:

For each section:
- Section name (e.g. "Section A — MCQ", "Section B — Numerical")
- Question type (MCQ / Multi-select / Short answer / Numerical / Coding / Subjective)
- Number of questions
- Marks per question
- Negative marking (yes/no, value if yes)
- Partial credit rules (for multi-select/subjective)

Global:
- Total duration
- Section-wise time limits (optional — lock student into a section within a sub-timer)

**Validation:** at least one section; total questions matches what step 2's source pool can supply.

---

## Step 4 — Question Generation & Review
- Generate questions per the above config
- **Per-question actions:** edit inline, regenerate single question, delete, reorder
- **Duplicate/near-duplicate flag:** visually mark questions with high similarity for
  instructor review before finalizing
- Preview mode: view exam as a student would see it

**Validation:** no unresolved duplicate flags; every question has a correct answer/rubric set.

---

## Step 5 — Security & Integrity Settings
- Question order: fixed / shuffled per student
- Option order (MCQ): fixed / shuffled per student
- Proctoring level: None / Webcam recording / Webcam + lockdown browser
- Access restriction: access code / IP allowlist / single-device lock
- Late submission: not allowed / grace period (specify minutes) with penalty (optional)
- Retake policy: not allowed / allowed (max attempts)

**Validation:** if proctoring enabled, confirm student consent/notice text is set.

---

## Step 6 — Assignment & Scheduling
- Assign to: individual / group / cohort / class
- Start date & time, end date & time
- Timezone
- Notification settings (email/in-app reminder before start)

**Validation:** end time after start time; assignees non-empty.

---

## Step 7 — Post-Exam Settings
- Result release: immediate / after deadline / manual release by instructor
- Show correct answers: yes / no / after release only
- Grading: auto (objective types) / manual (subjective) / hybrid
- If manual/hybrid: rubric input per subjective question

**Validation:** rubric required for any subjective question if auto-grading is selected for it.

---

## Step 8 — Save as Template (optional)
- Toggle: save this configuration (sections, weighting rules, security settings) as a
  reusable template for future exams
- Template name

---

## Step 9 — Final Review & Create
- Consolidated summary of all steps above, editable inline (jump back to any step)
- Explicit "Create and Assign" confirmation action

---

## Notes for implementation
- Steps 2–3 depend on each other: changing section structure in Step 3 should
  re-trigger the question pool check from Step 2 rather than silently failing later.
- Step 4's duplicate detection should run against both the current exam draft and
  previously created exams tagged for exclusion in Step 2.
- Consider persisting draft state at every step so an instructor can exit and resume.
