# Pudding & Pickle's A Year of Adventures: KDP Listing Kit

## Files to upload

| File | What it is |
|---|---|
| `Pudding-and-Pickle_Interior_8.5x11.pdf` | Interior manuscript: 132 pages, black & white, no bleed, all fonts embedded, line art at 500 dpi |
| `Pudding-and-Pickle_Cover_FullWrap.pdf` | Full-wrap cover (back + spine + front): 17.5473 × 11.25 in at 300 dpi, includes bleed |
| `cover_front.jpg` | Front cover only, for ads, social posts and A+ Content |
| `interior_preview.jpg` | Sample interior pages, for A+ Content and social posts |

## KDP paperback settings (these must match the files)

- **Ink and paper:** Black & white interior, **white** paper. The spine width was calculated for white paper; cream paper would make the cover the wrong size.
- **Trim size:** 8.5 × 11 in
- **Bleed:** No bleed (for the interior)
- **Cover finish:** Glossy (best for kids' books, makes the colors pop)
- **Page count:** 132 (spine = 0.2973 in)
- **Barcode:** Let KDP add it. The bottom-right of the back cover is left clear for it.

> If you add or remove interior pages, the spine width changes. Re-run `build/build_cover.py`; it reads the page count from the interior PDF.

## AI disclosure (required by KDP)

KDP asks if AI was used. Answer **Yes**. The illustrations (interior and cover) and the text were AI-generated, then curated and edited. AI-generated content is allowed on KDP, but you have to disclose it.

## Title fields

- **Title:** Pudding & Pickle's A Year of Adventures
- **Subtitle:** Coloring Book for Kids Ages 2-6: 60 Big & Easy Pages of Cute Corgi Puppies Through Every Season, Single-Sided Pictures for Toddlers & Preschoolers
- **Series:** Pudding & Pickle Coloring Adventures (Book 1)
- **Author:** your name or pen name. The copyright page currently says **Edson Aguilar Salazar**. To use a pen name, change `AUTHOR` in `build/build_interior.py` and rebuild.
- **Reading age:** 2 to 6

## Description (paste into KDP; HTML is allowed)

```html
<b>Color your way through a whole year of fun with Pudding &amp; Pickle!</b><br><br>
Meet <b>Pudding</b>, the brave big-brother corgi with his lucky star bandana, and <b>Pickle</b>, his giggly little sister with the floppy ear and polka-dot bow. Together they build a snow-pup in January, fly kites in March, dig up pirate treasure in July, jump in autumn leaves in October and celebrate the New Year in December, with <b>5 adventures for every month</b>.<br><br>
<b>Why kids (and grown-ups) love this book:</b>
<ul>
<li><b>60 original coloring pages</b>, each printed on its own page so markers never bleed through to the next picture</li>
<li><b>Big, bold, easy lines</b> made for little hands, toddlers and preschoolers</li>
<li><b>A read-aloud story line on every page</b>, so coloring becomes storytime</li>
<li><b>A fun color challenge on every page</b> (count the snowflakes, make the rainbow, give the giraffe spots) to build early learning skills</li>
<li><b>Bonus pages:</b> "This book belongs to" page, color tester page, draw-yourself page and a Super Colorer award certificate</li>
<li><b>Large 8.5 x 11 inch pages</b>, perfect for crayons, markers, pencils and paint sticks</li>
</ul>
A perfect gift for birthdays, holidays, Easter baskets, road trips and rainy days. Ideal for boys and girls ages 2, 3, 4, 5 and 6 who love puppies!<br><br>
<b>Collect the series:</b> Pudding &amp; Pickle Coloring Adventures, Book 1.
```

## 7 keywords (one per KDP box)

1. corgi coloring book for kids
2. puppy coloring book for toddlers ages 2-4
3. dog coloring book for kids ages 4-8
4. seasons coloring book for preschool
5. big easy coloring pages for toddlers 1-3
6. cute animal coloring book for girls and boys
7. simple coloring book for 3 year olds

**Never put "Bluey" (or any other show or brand name) in the title, subtitle, keywords or description.** KDP rejects these and can suspend your account for trademark misuse.

## Categories (KDP lets you choose 3)

1. Children's Books › Activities, Crafts & Games › Coloring Books
2. Children's Books › Animals › Dogs
3. Children's Books › Early Learning › Basic Concepts › Seasons

## Pricing

- **Suggested list price: $9.99 USD.** On Amazon.com, KDP pays 60% royalties at $9.99 and above, but only 50% at $9.98 and below.
- An 8.5 × 11 black & white book with 132 pages costs roughly $2.60 to $3.30 to print. That puts your royalty at about **$2.70 to $3.40 per sale** at $9.99. Check the exact figure in KDP's pricing calculator when you set up the book.

## Before you hit Publish

1. Upload both PDFs and open the **Launch Previewer**. Check that nothing important is flagged near the edges.
2. **Order a printed proof copy.** It's cheap and lets you see the line thickness and cover colors on real paper.
3. After launch, add **A+ Content** using `interior_preview.jpg` and `cover_front.jpg`.

## Getting repeat buyers

Pudding & Pickle are your own original characters, so you can build a series that owns its brand:
- **Book 2 ideas:** *Pudding & Pickle Go to School* (letters/numbers), *Pudding & Pickle's Holiday Fun*, *Pudding & Pickle Around the World*, *Pudding & Pickle Meet the Dinosaurs*.
- Keep the same cover layout (rainbow title, pink spine, "COLORING BOOK" label) so they look like a set on the shelf. Set **Series** on every book so Amazon links them together.
- The last page of this book already teases more adventures, and the back cover says "Collect the series!".

## Rebuilding (if you change anything)

All the art and fonts are saved in `build/assets/`. From the `coloring-book/build` folder:

```bash
python3 build_interior.py assets/clean assets/fonts ../Pudding-and-Pickle_Interior_8.5x11.pdf
python3 build_cover.py assets/cover_art assets/fonts ../Pudding-and-Pickle_Interior_8.5x11.pdf out
cp out/cover.pdf ../Pudding-and-Pickle_Cover_FullWrap.pdf
```
Story captions and color challenges are in `build/pages.py`. Fonts: Luckiest Guy, Fredoka and Nunito (all SIL Open Font License, free for commercial use).
