"""Resume & cover letter generator (Phase 10).

The pipeline finds and scores postings; this package turns a
specific posting into a Claude-ready prompt for writing a
tailored resume or cover letter, plus a renderer that produces
ATS-friendly .docx output.

Modules:
  posting_gap_analyzer  — Step 0: per-posting gap analysis via Gemma
  prompt_builder        — Step 1: resume prompt builder
  cover_letter_prompt_builder — Step 2: cover letter prompt builder
  docx_renderer         — Step 3: ATS-friendly .docx rendering
"""
