# How the pipeline processes documents

This document records the **method** only. What was measured and how much came out is
information about a source document, so it is not written here. That covers ranges, counts,
labels, sheet names and elapsed time. The confirmed behavior and rules are in
[`../finalized_source.md`](../finalized_source.md). Future work is in
[`roadmap.md`](roadmap.md).

## Why it ended up this way

This section lists the points where the work got stuck and how each was resolved, in order.
Dates and figures are omitted.

### LibreOffice was removed from the renderer

The path that converted Office files to PDF and then rasterized them split one sheet across
several pages. The reference for turning cells back into pixels was lost, so the position of
a table or picture could not be pinned to a page.

The renderer now parses OOXML directly in a browser and draws it on a Canvas. It records the
column and row edges as it draws, so cell→pixel is a **lookup**, not an estimate. In XLSX, one
sheet maps to one page. DOCX and PPTX use the same renderer.

### A picture does not fill the cells it is anchored to

Measuring a picture by its cell range puts bands of empty sheet into the crop. The pipeline
uses the DrawingML `ext` instead. For a picture with `editAs="oneCell"`, the `to` marker is
stale and cannot be trusted.

### Things that overlap are treated as one block

A single picture is often attached as several pieces, or a photo sits inside a table. Making
a node per piece produces a graph nobody can read.

Union-find groups whatever overlaps or touches into one region, and one member represents the
region: the table if there is one, otherwise the top-left picture. Comparison tables placed
side by side stay separate even if they touch. If one encloses another, it absorbs it.

### There must be only one place that makes crops

The same merge was done in two places, and they disagreed on the representative of the same
picture. One merged by the real DrawingML extent, the other by cell range. As a result, the
model was asked to identify a picture and was sent **the crop of a different picture**.

There is now one producer. The binding stage cuts the crop, stores it in an asset, and records
the ownership as an edge. Every reader uses `read_node_crop()`. It takes a node id, not a box.
The reading function lives in the same module as the writing function. If they are apart, they
diverge again.

This problem happened twice. If a new consumer tries to cut straight from the page image, that
is the next mismatch.

### Markers do not build a hierarchy

There was a pass that guessed indentation levels from symbols such as `①`, `▶` and `※`. A
symbol is a display character, and authors do not use it consistently. An untrustworthy
hierarchy is worse than none, because whoever reads the graph relies on it. The pass was
removed, and the default is to build no outline.

### The hierarchy is read from the rendered page

When it is on, the model looks at the page image and decides each unit's parent. The request
carries the unit list and the page image. It does not carry parent links or the heading and
caption classification, because those are the questions being asked.

### Do not ask for what the model cannot copy

Two things failed.

**Identifiers echoed back in the response.** The response was required to include the request
id and the image hash, and these were compared. The schema kept requiring them even after the
prompt stopped supplying them. The model had to invent them, and the comparison failed every
time. Even when they match, nothing is proven, because the side that sent the request already
knows which request it was. They were removed from the response.

**36-character UUIDs.** The model joined the first part of one unit's id to the last part of
another's. The hierarchy choice was right and the transcription failed. All retries were spent
on the same clerical task. Ids are now shortened to the first 8 characters and extended only
when they collide within a scope. The adapter restores them, and the domain sees only full ids.

### Do not send values the model cannot use

What was removed from the payload, and why:

| Removed | Reason |
|---|---|
| `bbox` | Text is positioned by cell, so it does not match where overflowing characters actually are. The page image already shows the layout. |
| `requires_decision` · `eligible_parent_ids` | Every unit needs a decision and all sit under the same root. A value that applies to everything distinguishes nothing. |
| `evidence_ids` | Opaque strings that can only be chosen without knowing their content. The requirement to cite evidence was removed with it, because without sending evidence it could not be met. |
| `order` | The array is already in reading order. There is no reason to write what position already says as a field. |
| document and request identifiers, image hash | These are values for the side that matches and verifies the response. |

A table carries no crop. Its label carries the header row and the cell range. Only a picture
or chart ships with a crop. It is the one type whose label, "Figure N", does not say which
figure it is. The crop is attached right after `unit <short id> (<label>):`. The order is the
link, and there is no separate reference key.

### Ownership and hierarchy are different things

Traces of the extractor judging text were scattered across the four formats. Only for
worksheets had it been decided that "text is reported as text", and the decision was not
carried to the others. Slide title placeholders, Word heading styles and large type in a PDF
still became HEADING, and the PDF built a SECTION tree from font size.

The worksheet was more deeply hidden. Its node type was already TEXT_CHUNK, but code that built
a heading stack from cell markers and made every table, picture and text **own (CONTAINS)**
under the deepest heading was still running. "The marker-based hierarchy was removed" applied
only to the outline projection. Also, the retrieval subgraph falls back to ownership when there
is no derived hierarchy. So under the default setting (`off`), the retrieval context was exactly
that marker tree.

Now in all four formats, text is TEXT_CHUNK, and ownership holds only what natively contains
something. Only the hierarchy pass decides what reads under what. There is no longer a HEADING
node for an observation the model saw as a "title" to attach to, so observation matching was
changed to attach to the text. Otherwise that observation would be dropped silently.

### Slides are read by the same pass

For a while the hierarchy pass handled only worksheets. The reason was cell geometry: the only
way to know where a unit sat on the page was a cell lookup.

Once the question of position was gathered into one place, the reason disappeared. A sheet
answers from the column and row edges the renderer recorded. A slide answers from the rectangle
the shape already has. Everything below that (merging, crops, scope assembly, what the model is
asked) is the same code on pixels. Supporting another format became a matter of adding one
placement method.

The absorption rule does not differ by format either. The text, tables, charts and shapes that a
region's representative swallows are part of it, and swallowed things are not sent as separate
units. Sending them would be asking where to place something that is already part of another.

### Everything a page holds stays in the graph

A page always carries things unrelated to what the document means to say: page numbers, a
footer repeated on every page, template watermarks, decorative rules. For a while the hierarchy
decision had a `drop` option, so the same answer that chose a parent also said "this is not
content". A dropped node was removed together with what it owned, and the reading order was
joined back up.

It was dropped once. The reason was that the judgment is made once, a wrong call cannot be
undone, and no later stage can bring back a node that is gone. During that time every unit the
file gave was kept. Page numbers gathered into passages, and in a linked document far from the
question, such a passage surfaced as the "nearest passage". So `drop` was brought back. The
prompt says to remove only what would be noise if a search returned it, and states that title,
author, date, document number and caption are content. A second filter applies when passages
are cut: a group with no letters at all, and a number standing alone directly under the
document, do not become passage text (the node stays and the passage covers it).

### One reading cannot verify itself

The hierarchy reading is one call, and the only thing that can say whether its answer is right
is the same answer. If the same page is read twice, some places differ, and those places are
exactly where the page is ambiguous.

So the reading is run N times in parallel, and **if all agree, it is used as is**. If there is
nothing to settle, there is no call. Only when they differ, a third call sends the page image
and the two outlines in the same short ids and has the model pick one.

There is no partial merge. Stitching together pieces that look good from two readings gives a
third hierarchy that matches neither reading, and nobody judged it. The question is "which one
matches the page".

We measured whether concurrent calls are blocked at the model endpoint. They are not. The
requests are actually processed overlapping, including page-sized multimodal requests. However,
under overlapping load the model endpoint sometimes returned 408, and that one error failed a
whole page. 408, 429, 502, 503, 504 and transport errors say nothing about the page, so they
are retried at the same level as a format violation.

### A continuous document cannot be cut into pages

A sheet and a slide are units the author worked in, so one page is one tree. A DOCX or PDF page
is only where the paper ended, and the first line of a page is usually under a heading printed
on the previous page. Deciding each page separately fragments the document.

So pages were read in order without breaks. Each page scope was sent **the hierarchy built by
the earlier pages** and **the bottom image of the previous page**. A unit carried over could only
be named as a parent and was not decided again. The root is the document node, not a page. (The
order was later given up for reading pages side by side and joining the breaks: see "Pages read
on their own, joined at the breaks".)

Labels alone were not enough. When the "open items from before" were passed by name only, the
model put a table that fills the whole page at the root and answered that "no higher-level item
to attach to was provided". That answer was correct. The prompt explained that key, but the
payload did not contain it. Once it was added, the same table attached under the relevant
section of the previous page. **What the prompt talks about must be in the body.** That is why
there is a rule to intercept and check the HTTP body the adapter actually sends.

When a paragraph or table straddles a page boundary, it is decided once, on the page that shows
more of it. Asking twice can give two answers that disagree, and if they disagree neither has
grounds.

### A flow document does not state coordinates

A DOCX file has no coordinates saying "this paragraph is here". Layout decides, and positions
move together when something above changes. The renderer was asked. The viewer returns the runs
for each page, and each run carries the **body index** it came from. A paragraph is the union of
its runs. A table is the union of the paragraphs in its cells, so the whole table comes out at
once. A picture without text has no runs, so an element hit test is thrown only at positions
with no text, and it returns exact bounds.

A PDF is the opposite: it is already drawn. Extraction records a normalized bbox for each block,
so the same placer as slides is used, and pymupdf does the rasterizing.

### Things the document shows that were not in the graph

- **Word text boxes.** A report title and the authoring department are usually floating text
  boxes. `document.paragraphs` walks only the body and does not enter boxes, so the title was
  absent from the graph altogether. Each box is extracted as one unit, the same treatment as a
  slide's text box. Word writes the same box a second time for older readers (`mc:Fallback`),
  so that copy is skipped.
- **A PDF's border.** A border around a report parses as a table, and the page's paragraphs end
  up whole inside its cells. Left alone, one page of the document becomes one table node. A real
  table has most cells filled and each cell is a value. A frame is less than half filled and one
  cell is a paragraph. It is judged not a table only when all three (area, fill rate, longest
  cell) agree.
- **A table with no text.** A table holding only photos is content, but a table that has no text
  and also wraps everything else on the page is a border. The criterion is the combination of
  these two, not size.

### A paragraph's box must be only as large as the paragraph's text

The renderer for flow documents reports pictures and floating shapes under **the body index of
the paragraph they hang from**. When these were merged under the same key as the paragraph's
runs, the paragraph holding a picture grew to the picture's size, and by the land-grab rule the
neighbors in that band were absorbed by the picture. On screen the text looked as if it had
vanished. Extraction was fine; the placement was wrong.

A paragraph is the union of its own text, and a picture is where it is drawn. The two are
counted separately.

We also record how we confirmed that text was missing: for each `w:p` in the document XML we
extracted the text and compared it line by line with the graph's node text. Only the footer page
number was missing. Text that looks gone on screen and text missing from the graph are different
problems.

### Indentation is used in two ways

Indentation is the strongest signal for deciding the hierarchy. But the prompt said "a marker is
only display text and is not grounds for hierarchy". That rule was added because of `①▶※` in
XLSX, whereas in flow documents the difference between `-` and `ㆍ` was exactly that signal. It
was corrected: a marker **alone** is not grounds, but it is grounds when it changes together
with indentation.

That was still not enough. One document used **spaces before the sentence** for indentation
instead of paragraph settings, and the extractor erased those spaces with `text.strip()`, so
neither the model nor we could see them. We now record the number of leading spaces, rank it
within the page together with the measured left coordinate, and send it as `indent`. Distances
differ by document, so only the order is sent.

### Pages are mostly margin

Sending a rendered page as is encodes the paper's margins too. The page is cropped to its ink
bounds, leaving 5%. Only the picture sent to the model is cropped. Crops are cut from the
original page with the stored box, so nothing that speaks in coordinates moves. The image hash
and size carried by the request are recomputed for the cropped picture.

### A PDF calls its border a table

A border around a report is parsed as a table, and once the table finder finds the outer border
it does not look inside. So one page became one table node and the real table was not found.
When something is judged a frame, it is not reported and **its inside is scanned again**. A
frame is told apart by contents, not size. A real table has most cells filled and each cell is a
value. A frame is less than half filled and one cell is a paragraph.

After finding the table, a second problem remains. pymupdf groups text into horizontal bands, so
a paragraph on the left and a table cell on the right become one block. Left alone, the
paragraph holds the table's values, and it covers the table and is absorbed into it. The lines
that fall inside the table are removed from the block, and the paragraph is built from the rest.

### A PDF's shapes are not nodes

The borders and arrows of a diagram are vector paths. They do not become picture nodes, so the
crop held only the photo pieces, and a reader could not tell what the picture connected to what.
A picture takes in the vectors it touches, and the chain continues through whatever the taken-in
things touch. A page's layout is also drawn as vectors, so the cut is made by size: only vectors
smaller than 10% of the page area and smaller than 60% of its width and height can belong to a
picture.

### An id that lost a character in transcription

A long document has many pages, and the ids are copied by hand on each page. An 8-character short
id came back as 7 characters now and then, and each time it used up 3 retries and lost a whole
page. Such an id is read as a unit only when exactly one unit matches. If it matches two or more,
it is rejected, because a wrong parent is worse than a missing page.

There were also cases where repeated readings of the same page returned an id wrong in **only the
last two characters**, again and again. The one-character-difference rule did not catch it, and
the whole document's hierarchy came out flat. If the first six characters match exactly and only
one unit fits, it is read as that unit.

### A failed page is recorded in the result

After making a page that got no answer skip just that page, one problem remained: the skipped
page was indistinguishable from a "page read as flat". In the demo that page looked complete, and
the index showed no trace of it. So the hierarchy pass returns the pages it could not read along
with its decisions (`HierarchyDerivation.unread_pages`), and the graph build records them as a
warning.

### Output in a shape Neo4j can store

The implementation we compared against mapped node types to labels and edge types to relationship
types, and looked up relationships by id with no label. In Neo4j, a `MATCH` without a label scans
all nodes. So every node gets the common label `DocNode`, and the uniqueness constraint is on
that label.

Only properties Neo4j accepts are sent. A list is stored only when its elements share one type,
so coordinates that mix integers and floats are made all floats, and other mixed lists are kept as
a JSON string. Otherwise the script stops at that statement. A null removes the key, so nulls are
left out. Metadata keys from the source get the prefix `meta_` so they cannot overwrite core
fields, and a backtick in a name is written twice so the name does not end there.

### The graph view draws one parent line per node

A sheet owns every unit on it. Drawing every ownership line hung a node that has a hierarchy
parent from the sheet with one more line, and the sheet looked like the parent of every node. The
view follows the same rule retrieval uses to follow a parent: the derived hierarchy parent if
there is one, otherwise ownership.

### Demo tools do not live in `/tmp`

The demo's copy of Playwright was in `/tmp`, and the system's periodic cleanup deleted files not
accessed for days, so all renders failed between two runs. The viewer bundle now goes to the
config default `~/.cache/graphme`, and Playwright uses the installation in the repository's
`frontend/`. The demo's model, model endpoint and number of readings are read from the same
settings as `build-graph`. Defaults only the demo had showed results the pipeline would not
produce.

### A table is measured by its frame, and only text is measured by its glyphs

"What is inside a table belongs to the table" did not hold on slides. The cause was the box, not
the rule.

- A slide shape was measured by where the renderer drew its glyphs. That is right for a text box
  (the box is as large as the text it can hold), but it was applied to tables too, so a row of
  photos with no text was left outside the table. Tables, pictures and charts are measured by
  frame.
- The renderer numbers layout and master shapes in its own space. A slide shape with a clashing
  number got a glyph box from an unrelated place, and a page-number box got a box covering the
  whole slide and owned everything. A glyph box is used only when it lies inside the shape's own
  frame.
- A region was named after the largest thing that nearly fills it, and that could be text. Text
  is part of whatever encloses it, so it does not name a region that mixes other kinds.
- A DOCX table is the union of its cell text, so a row holding only photos drops out. Pictures
  and boxes drawn inside cells are added to the table's box.
- A PDF table's rules are also vectors, so a photo in a cell grew along the rules to the whole
  table and owned the table. A picture does not take in vectors inside a table's frame.

After fixing these, an error in the opposite direction showed up. A callout hanging over a table
header pulled the region upward, and a divider across below the title touched that callout and
the logo row, so the region grew to the top of the slide. Then even the slide title inside the
enlarged rectangle became part of the table. Two changes were made.

- Text belongs to a region only when it lies inside **each table's own frame, or the picture
  formed by merging the picture pieces**, not the region's rectangle. A label between the pieces
  of one screenshot still belongs to that picture, because the merged pieces are the picture.
- A line with almost no thickness is treated like an arrow. It can be absorbed by whatever
  encloses it, but it does not join the two sides just because it touches them. Whether
  something encloses it is likewise judged by the table's frame or the picture, as with text. A
  divider across the slide had been absorbed into a table because it lay inside the enlarged
  rectangle.
- Shape numbers also clash within one slide. A child of a pasted logo group had the same number
  as a diagram picture, so an arrow pointing at that number connected the logo to the diagram too,
  and the logo became the diagram's name. When more than one shape has the number, only the one
  the arrow touches is joined.
- "The arrow connects them" means they are read together, not that one is inside the other. A
  label outside a diagram was joined by an arrow and became part of the diagram, and when only one
  end was text, the text owned the arrow. A connector joins only pictures and tables to each
  other.

The check counted every table and picture in the four formats in two directions: whether what
lies inside its own frame ends up owned by it, and whether the text it owns actually lies inside
its frame or parts.

### Two documents rendered at once got each other's pages

The capture script copied a document to a fixed name (`sample.<format>`) in the viewer bundle
folder and rendered from there. When two renders overlapped, the later copy overwrote the earlier
input, so one document got the neighboring document's pages, and with several overlapping, pages
went missing. There was no error. Ports were also chosen at random from a narrow range and could
collide. Now each render renders from a site that serves the files of its own work folder ahead of
the bundle, and it keeps the document, pages and plan separate. The port is obtained from the
system. The copy of the last rendered document that used to remain in the bundle folder is gone
too.

### A photo table is not a border

The rule that sees a table with no text that wraps everything on the page as a border also made a
textless table holding only photos a border. The table then became the background, and the photos
were scattered outside it. A border goes around a report, so it holds text or tables. A table
holding only pictures and charts is not seen as a border.

### The viewer library also breaks on real documents

A paragraph that holds only a picture has one empty piece that only measures height, and that
piece has no character class (`script`). When measuring a table cell's width, the code builds a
fingerprint to merge adjacent text pieces, and it stopped at that empty piece, so the whole
document did not render. Even if it had merged, the "measure height only" mark would have carried
over to the neighboring letters and the letters would have disappeared. The npm release is used
as is, and fixes like this are kept as patches tied to the version (`viewer/dist-patches/`).
Raising the version makes us check them again.

### Photos inside a metafile drop out silently

A picture pasted as a metafile holds vector records such as lines and ellipses together with the
photo. The viewer's DIB decoder read only 32, 24, 8, 4 and 1 bit and returned nothing for 16 bit,
so only the picture's border and circle were drawn and the photo was missing entirely, with no
error or warning. A failed decode means "not drawn", so on screen the render looks finished. A
16-bit `BI_RGB` is RGB555, so it is now read the same way as the other depths.

The release carries two copies of the metafile player: a chunk the viewer uses on the main
thread, and a copy for workers that this renderer does not use. The patch reaches the first. To
find out which copy actually runs, we **fixed it and checked whether the result changed**:
breaking the worker entirely left the render unchanged.

### One border cell deletes a table

Table recognition picked candidates by their borders and let a rectangle pass only if its whole
perimeter was drawn with no cell missing. In a document where one cell of the last row had no
formatting, the whole table was rejected, and that sheet came out as dozens of cell-level text
pieces instead of a table, with the rows and columns gone. One empty cell is common in a table a
person drew. Now a side counts as a border even if it is broken for up to a share proportional to
its length (10%), and a short side still needs all of it.

### The range Excel declares is not the sheet's size

Excel writes a dimension on its own that includes places the document does not use. Some sheets
declared a whole column. Scanning that range cell by cell makes openpyxl create and discard a cell
object for every position, and most of one workbook's extraction time went there. Only the cells
the file actually wrote are read, in reading order. A cell with a border and no value is also
something the file wrote, so it comes in as is. A drawing's coordinate axes are measured only as
far as the anchors reach and are extended in the last size beyond that. That the answers did not
change was confirmed by comparing the extracted graphs of the whole corpus before and after the
fix.

### What a page handoff needs is the hierarchy, not a chain

When the handoff carried only "the chain of ancestors still open", the next page re-decided the
top levels of the document without knowing **what those ancestors already hold**. A section
heading of the same shape went under the title on one page and under the document number on the
next. So what is handed over was changed to **the hierarchy built so far itself**. A heading
arrives with what reads under it, and that is the only ground for saying it is the same heading.

The hierarchy handed over keeps **what it keeps whole**. Three things go out. The **open chain**
is the path in the document up to where the previous page stopped. Since it is where this page
attaches, it always goes, even with its branches cut. Next to it goes the **heading skeleton**:
units that have children, shallowest first, only those whose parent is already carried, up to half
of the budget (`SKELETON_SHARE`). Filling only the newest sections made the early chapters of a
long document vanish from the reference, and later pages were read without knowing the shape of
the document. With the remaining budget goes a **complete subgraph**: a node goes with all its
descendants or not at all. A heading that arrives with its children left behind reads as "a
section that starts there and holds nothing", which is worse than not sending it, and a node that
arrives with its parent left behind looks like a place to attach but is not one.

The budget is 1,200 tokens (`CARRY_TOKEN_BUDGET`). That is twice a search unit, because this is
the **reference frame** a page uses to check its own units. Korean is about one token per
character, so counting bytes ÷ 4 gives a third of the real figure and the budget would never be
reached. Hangul is counted separately. The newest subgraphs are kept first. A section decided two
pages ago is where this page continues, and a section from the first page of the document is not.
A subgraph that cannot get a place is left on its parent as a count (`outline_omitted`). Without
that, a section with its front part cut would read as "a section that began here".

Also, when a page ended inside a figure, the innermost item of the chain became the **label
printed on the figure**. The prompt says "it usually continues under the innermost", so this
pushes the first line of the next page under a figure label. Whatever hangs on a figure closes
with the page, so the chain is cut at the innermost figure.

### Pages read on their own, joined at the breaks

Reading in order made a document as slow as its chain of pages, and a page's mistake went on
into every page after it as part of what it was handed. So a flowing document is now read in
three steps. Its **headings** are asked once, on text alone, of every unit on a line. Every
**page** is then read side by side, shown the headings before it and the foot of the page
before. What a page decides inside itself is kept; a parent it names on another page is not,
because that is the third step: every **page break** is a small text question of its own, all
asked at once. The page's top units -- nothing above them on the page -- each answer a place and
`under` (opens inside it) or `beside` (the next item of its kind, which closes it). Beside takes
its neighbour's parent, which is known only when every break is answered.

What a break is offered decides most of what it answers. Place 0 is the document's title, the
first page's top that holds the most of it: taken from the skeleton instead, it once made the
title and the organisation line each other's parent. Then the sections open where the page
begins, walked up from where the page before stopped -- through that page's reading, then the
headings. Built from the last heading alone, the frame offered a cover's subtitle as a section.
Then how the page before ended: its last unit's chain and its other tops, since a page's later
top may have been joined under its earlier one. And what the page before was offered: what is
open where a page begins was open where the page before began, or that page opened it. Where a
page's re-printed chapter header had been dropped, the headings' guess alone left the chapter
out, and the next chapter went under a sub-item. A top with no heading above it takes the last
heading before it -- once: guessing again walked back through every chapter of the document.

Each place shows the first few things it holds, and how many. Without them a cover's date or
organisation line read as a section, and a wrong `under` there carried every later chapter
down through the beside chain. The prompt says so too (a byline, organisation, date or version
is not a section), and a shorter wording of the same rules lost it again. Tables, pictures and
dropped units are never places. A break is read twice, a third time where they differ, and the
answer most readings gave is kept; with no majority, the shallower one. The answers are text
and short, but the call reasons before it answers, and asked not to it put headings under
headings of their own kind.

### A page is asked about in three passes

When grouping figures, grouping text and the hierarchy were asked in one call, each answer had to
be the other's input. A label written on a figure belongs to the figure but was grouped into a
paragraph, and deciding parents over half a paragraph placed the half. So the order is fixed:
**figure → paragraph → hierarchy**. The figure first takes the words written on it, the remaining
pieces complete the paragraphs, and only then are parents decided over complete units.

The hierarchy call no longer asks about figure grouping. The earlier pass had already answered it
and nobody read that answer, yet each page paid for that instruction and output. And the
instruction "decide the parts under the figure too" became contradictory once parts were no longer
sent. A part's parent is a rule: the build places it once under the figure, without asking the
VLM. When figure labels were sent to the hierarchy one by one, most of a page's units were labels,
and the model placed them between body paragraphs.

### A two-column page must not be read top to bottom

Sorting by the top coordinate of the box alone makes a two-column paper read in a zigzag. The head
of the right column came before the end of the left column, and a section that started at the top
of the right column arrived before the heading at the foot of the left column and attached to the
previous section. The page is cut into bands wherever something spans the page, within a band
things that overlap in x are grouped as one column, and each column is read to its end
(`reading_order.py`). Text grouping and the hierarchy use **the same function**. When only one
side was fixed, the hierarchy side kept sending things in a zigzag.

Indentation had the same problem. Ranking over the whole page makes every line of the right column
look indented more than the left column. Indentation is measured **from its own column's left
edge**.

### A long answer does not reach its end

When all the printed lines of a page were sent in one call, the answer came back grouping only the
first few lines of a paragraph and dropping the rest, and the place where it broke off differed
each time the same page was read. The number of pieces asked at a time was reduced
(`PIECES_PER_ASK`), and the last few pieces of the previous call are handed over as already
decided (`CARRY_PIECES`) so that the groups continue across the boundary. Small print is left out
of the pieces handed over: a page that ended in footnotes handed over only footnotes, so the last
line of the body never met the rest on the next page.

The answer lists the piece ids for each group. Taking ranges (first and last) made the answer
shorter but made over-merging cheap, and one range swallowed several paragraphs. The cost each
piece adds to the answer is the device that keeps a unit from growing past its end. The request
text must also say the same thing as the system prompt: a request that says "give every piece a
group" makes the answer long, against an instruction that says "report only what has several
pieces".

When one group is wrong, **only that group** is discarded. Discarding the whole call because of an
overlapping group or one unrecognizable id left a page's paragraphs scattered one line at a time.

### A continued paragraph is read where it starts

When the box of a paragraph that continued into the next column or page was taken as the union of
all its pieces, it became a box covering the page width and was read at the top of the continued
place. The first paragraph of a section arrived before the headings printed above it. A unit
lives where its first piece is. The box is measured only from the first piece's page and the
pieces in its column.

### Footnotes are read after the page ends

When footnotes and page numbers stay where they are printed, they get between the last line of the
left column and the first line of the right column, and two lines that continue each other never
meet. Type smaller than the page's body is sent to the end of the page. But "small" alone does not
decide it. A small note in a report sits right under the paragraph it explains, and sending it to
the end separates it from the line it explains. Only small type **with no body text above or below
it within the same width** is an appendage of the page. The first rule, which decided by column,
saw the short second line of a note on a one-column page as a separate column and sent it to the
end alone.

### The bands of a split table belong to the table

A table crossed by a divider row is parsed as two or three tables, and the divider row's text
remains as text between them. Even when figure grouping merged the table pieces into one table,
that text hung outside the table. Text on the same page that lies inside the frame formed by
merging the table pieces is treated as a part of that table. This rule is not applied to pictures:
body paragraphs can stand between two photos of one picture, but not inside a table's frame.

### The document title holds the document

On the first page where the title is visible, a chapter sometimes went under the title and
sometimes to the document root. One rule was added to the prompt: the document title holds the
authors, the abstract and every later section, and beside the title at the top level stands only
what belongs to no part (running header, page number, watermark). It is not a rule for a
particular kind of document but for every document that has a title.

### Appearing by name is not citing

A reference is the only line that connects another document with this one, so a wrongly drawn line
ties two unrelated documents together. When the model was asked for "the name of another document",
it also answered with the names of methods and models that appear as the subject of a sentence.
So the condition was changed: it is a reference only when the page points to that document
**explicitly**. It must be one of four: wording that sends the reader to the document ("refer to",
"see"); a name used as a document (a report or standard number, a file name, a title, a
bibliography entry); a quotation with its source; or an address (URL, DOI, path).

Citation numbers were considered separately. `Method [7]` in a paper body is a method name with a
number attached, and the number only points to the bibliography. The document's name is given in
the entry `[7] … Method: 제목 …` of that list. At first a number was accepted as a citation, and
within one paper the same document was captured separately under each notation, external documents
multiplied, and even the row names of a results table became references. An answer whose only
ground is a number is accepted only when the unit **starts** with that number (an entry in the
list), and it is not accepted in tables or in text inside tables.

Saying it in the prompt alone does not show whether it was followed. So each answer must give its
**grounding phrase verbatim** (`cue`), and this side checks that the phrase and the document name
are actually in the unit's text. An answer that cites the name itself as its ground (except an
address), and a ground of fewer than two characters such as a single footnote digit, proves
nothing and is discarded. A figure unit without text has nothing to check, so its answer is
accepted as is. Checking requires the model to see the whole sentence, so the body of a text unit
is sent instead of the label of its first 80 characters.

### A node that points to another document becomes that document when it arrives

An `external_document` created by a reference starts out empty, because the document it points to
is not here and there is nothing to read. At first, when a document was added, if "a document with
the same name was already in the store", its id was written on the node. Then a cited document
that arrived later was never linked, and since lookup was by file name only, a citation by number
or title was missed.

So the linking was taken out of the step that builds one document and is computed over **the whole
corpus**. For each document it collects its identities (file name, the name without extension, the
document number inside the file name, the heading under which the hierarchy holds the document),
and links an `external_document` to the root of a document with `SAME_AS` only when its name
matches exactly one document. If two or more match, it does not link, because a guessed line ties
two unrelated documents together. Per-document graphs stay as they are and only the lines between
documents are added, so the result does not depend on arrival order and is the same when
recomputed.

### A folder becomes one graph in three steps

Making a folder into one graph has a fixed order: process each document, load all of them into
the same space, then link the external documents within that space. At first the links were
computed on the fly when the view was opened or exported. Then they were not kept anywhere and
vanished when the demo was restarted. Now the space is Neo4j and the three steps are explicit. The
link step reads the whole space, not the folder. A paper that an earlier memo only named is linked
the moment that paper is loaded later.

One document's failure is that document's failure. When the embedding provider rejected requests
(429) and the retries ran out, the whole folder stopped, because only a few exceptions were being
caught. Now, whatever the reason, only that document is left out, and it is recorded.

### There must be one place that says it is done

When the demo's read stage ended, it declared `done` itself, while the caption and reference
stages were still to run. The screen stops polling when it sees `done`, so it stayed holding the
graph from before the captions were attached. That is the cause of the symptom that looks like
"captions are not made". Declaring that it is done is now handled by the one runner that runs the
stages, and the screen fetches the graph once more at the moment it sees the end. The progress
status also describes everything up to the last stage in a sentence.

### Hybrid search: rank does not say whether anything was found

With vector search alone, for a question asking for a document number, the passage holding that
number was pushed behind passages that only sound similar. So the two Neo4j indexes, vector (ANN)
and full text (BM25, `cjk` analyzer), are each queried generously and combined with weighted RRF.
The library's max-normalization method is not used. Following research in English, the weight
leans slightly toward meaning (0.6), and it is flipped the other way when the question contains a
number.

"No relevant document" was first decided by the cosine of the single nearest vector passage. Then
even a question about one word that is in the document as written (like "part code") was thrown
out entirely because the cosine was below the line, and the passages the full-text search had hit
exactly vanished with it. The RRF score cannot decide it either. It is a score made from ranks,
so any question has a first place. Whether full-text search found something cannot decide it
either. Even an off-topic question shares one common word, and its BM25 score overlapped with the
score of a real word search and did not separate.

Now each passage is asked for its grounds when combining. The ground is one of three: cosine at or
above the line; it contains every word of the question (each word is searched against the passage
on its own, so a word the analyzer split must also match whole); it contains the document number
the question names. Only passages with grounds remain in the combined result, and if there are
none, nothing was found.

### A PDF that moves the pen instead of using spaces

Some PDFs do not use space characters between words and only move the position of the next
character. For a Latin font pymupdf puts a space in that gap, but not for a CJK font, so a Korean
report's sentences came out as one run without spaces. That is hard to read on screen and harmful
to full-text search, which cuts words into two-character pieces. Now every character of a page
goes through `page_text`. A gap between characters greater than 15% of the font size is treated as
a space. In an English paper the only places that changed were around math symbols (`∈ R`, `← f`),
and that is in the right direction too.

### Figure descriptions use the language of the body text, not the text inside the crop

"Write in the language of the page" was not enough. When an English paper included a Chinese
example page as a figure, the model looked at the characters inside the crop and wrote the
description in Chinese, and that description became a passage that could mix into an answer in
another language. The prompt now says to write in the language of the body text, and not to use
Chinese characters unless the body text is Chinese.

### Page noise is left to the VLM's drop

A watermark or a header icon repeated on every page sometimes was not `drop`ped and stayed right
under the document. A model that looks at one page at a time does not know that the element
repeats across pages. A proposal to filter by rule "the same text or picture repeated in the same
place across several pages" came up, but it was not adopted. A rule applied equally to every
document deletes content in some documents. When noise remains, no rule is added; the context the
model sees and the prompt are fixed instead.

## Working method

- RED → GREEN. Write the failing test first, and make the failure show up as a difference in
  behavior, not as a collection error. Twice a RED passed, and each time the fixture had not
  reproduced the real mismatch. Fixing the test so that it fails comes first.
- After changing a payload, intercept and check the HTTP body the adapter actually sends. A copy
  built by duplicating the prompt cannot catch a drift from the adapter.
- When doubting the reading quality, do not look only at the payload and the result tree; **look at
  the rendered page too.** There was a case where a call was named a misjudgment because the source
  had not been looked at enough.
- Measured values are not kept in the repository. The method of checking is in the verification
  section of `finalized_source.md`.

## Handling confidential information

Information that comes from a source document does not go into the repository. That covers sheet
names, item labels, cell ranges, counts and elapsed time. These are facts about that document, not
about the method.

- `.gitignore` blocks `/test_dataset/`, `/reports/` and `/data/`.
- `/tests/` and `/docs/progress-local.md` are also local only. Both hold measured values.
- Provider calls go out only to endpoints that `model_endpoint.py` allows, and only after checking
  that the document is allowed to be sent.
- `.env` and credentials are neither printed nor committed.
