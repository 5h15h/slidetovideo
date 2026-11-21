#!/usr/bin/env python3
"""Build a sample deck with terse speaker notes, for testing the pipeline."""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN

BG     = RGBColor(0x0E, 0x17, 0x26)
INK    = RGBColor(0xF4, 0xF7, 0xFB)
MUTED  = RGBColor(0x9F, 0xB3, 0xC8)
ACCENT = RGBColor(0x4C, 0x9A, 0xFF)

SLIDES = [
    dict(title="Rethinking Onboarding",
         sub="Why the first seven days decide the next seven months",
         bullets=[],
         notes="Open with the headline number: 68% of churn traces back to week one. "
               "Frame the talk: three findings, one proposal. Keep it tight, 12 min."),
    dict(title="The problem in one chart",
         sub="Activation has been flat for five quarters",
         bullets=["Signups up 34% YoY",
                  "Activation flat at 22%",
                  "Day-7 retention down 4 pts",
                  "Support tickets up 61%"],
         notes="Signups are not the problem - top of funnel is healthy. "
               "Activation is the bottleneck. Note the tickets number: people are "
               "trying and failing, not leaving quietly. Pause here, let it land."),
    dict(title="What we learned watching 40 sessions",
         sub="Three failure points, over and over",
         bullets=["Empty state gives no first move",
                  "Setup asks for data users don't have yet",
                  "Value is behind an integration step"],
         notes="We watched 40 recorded sessions. Same three walls every time. "
               "Empty state: users land, nothing to click. Setup: we ask for the org "
               "chart on day one - nobody has it. Integration gate: the magic moment "
               "is locked behind connecting a tool, which needs IT approval. "
               "That's a week of dead air."),
    dict(title="The proposal",
         sub="Deliver value before configuration",
         bullets=["Seed a working sample workspace",
                  "Defer every optional field",
                  "Ship a no-integration demo path"],
         notes="Invert the order: value first, configuration later. "
               "Sample workspace means day-one usefulness with zero setup. "
               "Defer optional fields - we counted 14, only 3 are needed to start. "
               "Demo path lets people see the payoff without waiting on IT. "
               "This is a 6-week build, two engineers."),
    dict(title="What we expect to see",
         sub="Modelled on the three pilot cohorts",
         bullets=["Activation 22% to 31%",
                  "Day-7 retention +6 pts",
                  "Setup tickets down by half"],
         notes="These are modelled, not promised - say that out loud. "
               "Based on the three pilot cohorts we ran in September. "
               "Activation is the number to watch; everything else follows it. "
               "Caveat: pilots were self-serve only, enterprise may differ."),
    dict(title="What we need",
         sub="Two engineers, six weeks, one decision today",
         bullets=["Approve the 6-week build",
                  "Name an exec sponsor",
                  "Review again at day 30"],
         notes="Close on the ask - be direct. Two engineers for six weeks. "
               "Need a sponsor to unblock the IT conversation. "
               "Offer the day-30 checkpoint so this feels reversible. "
               "End with the week-one number again: 68%."),
]

def style_bg(slide, prs):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = BG

def add_accent(slide, top_in):
    bar = slide.shapes.add_shape(1, Inches(0.9), Inches(top_in), Inches(1.15), Pt(5))
    bar.fill.solid(); bar.fill.fore_color.rgb = ACCENT
    bar.line.fill.background()
    bar.shadow.inherit = False

def main(path="examples/sample_deck.pptx"):
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    for i, s in enumerate(SLIDES):
        slide = prs.slides.add_slide(blank)
        style_bg(slide, prs)

        size = 46 if i else 54
        # Rough line count so the subtitle never collides with a wrapped title.
        cpl = int(11.3 * 72 / (size * 0.55))
        nlines = max(1, -(-len(s["title"]) // cpl))
        tb = slide.shapes.add_textbox(Inches(0.9), Inches(0.68),
                                      Inches(11.5), Inches(0.1 + 0.82 * nlines))
        tf = tb.text_frame; tf.word_wrap = True
        p = tf.paragraphs[0]; p.text = s["title"]
        p.font.size = Pt(size); p.font.bold = True
        p.font.color.rgb = INK; p.font.name = "Inter"

        accent_top = 0.80 + 0.82 * nlines
        add_accent(slide, accent_top)

        sb = slide.shapes.add_textbox(Inches(0.9), Inches(accent_top + 0.22),
                                      Inches(11.0), Inches(0.7))
        sf = sb.text_frame; sf.word_wrap = True
        sp = sf.paragraphs[0]; sp.text = s["sub"]
        sp.font.size = Pt(22); sp.font.color.rgb = MUTED; sp.font.name = "Inter"

        if s["bullets"]:
            bb = slide.shapes.add_textbox(Inches(0.9), Inches(accent_top + 1.15),
                                          Inches(11.0), Inches(3.6))
            bf = bb.text_frame; bf.word_wrap = True
            for j, b in enumerate(s["bullets"]):
                para = bf.paragraphs[0] if j == 0 else bf.add_paragraph()
                para.text = f"—   {b}"
                para.font.size = Pt(30); para.font.color.rgb = INK
                para.font.name = "Inter"
                para.space_after = Pt(20)

        slide.notes_slide.notes_text_frame.text = s["notes"]

    prs.save(path)
    print(f"wrote {path} ({len(SLIDES)} slides)")

if __name__ == "__main__":
    main()
