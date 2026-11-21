// One-page resume. Data arrives as JSON in sys.inputs.data (see resume/layout.py),
// so text is never interpreted as Typst markup.
#let d = json(bytes(sys.inputs.data))

#set document(title: d.name + " – Resume")
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.45in, bottom: 0.45in))
#set text(font: "New Computer Modern", size: 10pt, lang: "en")
#set par(leading: 0.42em, spacing: 0.42em, justify: false)
#set list(indent: 0.6em, body-indent: 0.45em, spacing: 0.32em, marker: [•])

#let section(title) = {
  v(6pt)
  block(spacing: 0.3em, stack(spacing: 2.5pt, text(size: 11.5pt, smallcaps(title)), line(length: 100%, stroke: 0.5pt)))
}

#let entry(e) = {
  block(spacing: 0.35em, breakable: false)[
    #grid(columns: (1fr, auto), align: (left, right), row-gutter: 0.32em,
      text(weight: "bold", e.left1), text(weight: "bold", e.right1),
      ..if e.left2 != "" or e.right2 != "" {
        (text(style: "italic", size: 9.5pt, e.left2), text(style: "italic", size: 9.5pt, e.right2))
      } else { () },
    )
    #if e.bullets.len() > 0 {
      v(-1pt)
      list(..e.bullets.map(b => text(size: 9.5pt, b)))
    }
  ]
}

#align(center)[
  #text(size: 22pt, smallcaps(d.name)) \
  #v(-2pt)
  #text(size: 9pt, d.contact)
]

#for s in d.sections {
  section(s.title)
  for e in s.items { entry(e) }
}

#if d.skills.len() > 0 {
  section("Skills")
  for g in d.skills [
    #text(size: 9.5pt)[#text(weight: "bold", g.name + ":") #g.items] \
  ]
}
