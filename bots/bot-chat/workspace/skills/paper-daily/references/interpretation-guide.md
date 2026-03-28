# Paper Interpretation Writing Guide

## Target Audience

RL practitioners who may lack specific sub-domain background. They understand basic RL concepts (MDP, policy gradient, value functions) but may not know the specific sub-area deeply.

## Tone

- Accessible but deep — "plain language with substance"
- Conversational but not dumbed down
- Show genuine enthusiasm when warranted, skepticism when appropriate
- No AI-speak ("pivotal role", "landscape", "delve into")
- No excessive emoji or formatting tricks

## Structure

### Title
Use the original paper title. Add a Chinese subtitle if helpful.

### One-sentence Summary
Capture the core contribution in one sentence a non-expert can understand.

### Background Section
- Start with the real-world problem or motivation
- Explain what approaches existed before and their limitations
- Make the reader feel the gap this paper fills
- 2-3 paragraphs, no jargon without explanation

### Method Section
- Lead with the core idea in one paragraph of plain language
- Then dive into technical details with figures
- For every key formula:
  1. Show the rendered formula image
  2. Immediately follow with "翻译成人话：..." paragraph
  3. Explain what each symbol means
- Use paper's architecture/method figures with annotations
- If the method has multiple stages, walk through them sequentially

### Experiments Section
- Rebuild key results as Markdown tables (do not screenshot tables)
- Pick 1-2 most compelling figures from the paper
- Highlight: what beats baselines, by how much, on what tasks
- Note any surprising results or ablation insights

### My Take Section
This is the most important section — it differentiates this from a mere summary.
- What does this mean for the RL community?
- What are the real limitations (not just "future work" from the paper)?
- How does this connect to other recent work?
- What would you try next if you were building on this?
- Be honest: if the paper has weaknesses, say so

### Footer
Include paper link, code link (if available), and publication credit line.

## Image Handling

- Figures: extracted from paper via fetch_figures.py, uploaded to WeChat
- Formulas: rendered via render_latex.py, uploaded to WeChat
- Tables: write as Markdown tables, rendered as HTML by the publisher
- Cover: auto-generated with paper title

## Length

Target 2000-3000 Chinese characters (roughly 6-10 minute read).
