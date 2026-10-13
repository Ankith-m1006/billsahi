# GST goods rates: provenance

File: `gst_goods_rates.csv`. Built 13 Oct 2026 from official CBIC notification PDFs. These are the rates in force on that date.

## Sources

All of these came from the CBIC Tax Information Portal (taxinformation.cbic.gov.in). The portal's download API is `https://taxinformation.cbic.gov.in/api/cbic-notification-msts/download/<record id>/ENG`, and it returns the PDF as base64. The PDFs were decoded and saved without changes to `raw/notifications/`. The file names, record ids, URLs and SHA-256 hashes are in `raw/notifications/manifest.json`.

| Notification | Date / in force | Role | Record id |
|---|---|---|---|
| 9/2025-CT(Rate), G.S.R. 641(E) | 17.09.2025 / 22.09.2025 | Rate schedules I-VII. Supersedes 1/2017-CT(Rate) | 1010436 |
| Corrigendum to 9/2025 | 18.09.2025 | Corrects the list labels on gazette p.88 | 1010467 |
| 10/2025-CT(Rate), G.S.R. 660(E) | 17.09.2025 / 22.09.2025 | Nil-rate (exempt) goods. Supersedes 2/2017-CT(Rate) | 1010437 |
| 14/2025-CT(Rate) | 17.09.2025 / 22.09.2025 | Bricks and tiles at 6% CGST (12% total) | 1010441 |
| 19/2025-CT(Rate), G.S.R. 946(E) | 31.12.2025 / **01.02.2026** | Amends 9/2025 (tobacco, pan masala) | 1010534 |
| 01/2026-CT(Rate) | 30.04.2026 / **01.05.2026** | Amends 9/2025 (2202 tariff items, aligned with Finance Act 2026). The portal file is "CTR-E-updated.pdf", described as "updated vide Corrigendum dated 06.05.2026" | 1010645 |
| 11/2025-CT(Rate) | 17.09.2025 | Amends 3/2017 (concessional supplies, petroleum operations). Not in the CSV | 1010438 |
| 02/2025 and 03/2025-Compensation Cess (Rate) | 17.09.2025 and 31.12.2025 | Cess set to Nil | 1010449, 1010537 |

There is one non-official file: `10-2025-CTR-corrigendum-MIRROR-gstgyaan.pdf`, from https://gstgyaan.com/pdf/notification-no-10-2025-central-tax-rate-corrigendum.pdf (sha256 025cc122...030f). The portal lists the 10/2025 corrigendum (record 1010472) with a Hindi file only, and its download API returned HTTP 500. The mirror copy is in Hindi. It corrects only the Hindi text (the word for "eraser" on p.6, and the layout of the puja samagri list on p.8). It changes nothing in the English entries, so it was not applied.

**No later amendments exist.** The portal's Central Tax (Rate) listing, fetched 13.10.2026, shows nothing after 01/2026. The Integrated Tax (Rate) listing matches it (19/2025-IT and 01/2026-IT). The 01/2026 PDF also confirms the chain: 9/2025 was "last amended vide 19/2025".

## How the CSV was built

Scripts are in `hsn/scripts/` and run with `.venv/Scripts/python -I`:

- `download_notifications.py` downloads the PDFs.
- `extract_text.py` extracts the text into `raw/notifications_text/`.
- `parse_rates.py` builds the CSV and writes `raw/parse_audit.json`.
- `verify_rates.py` checks the CSV against the PDF text.

**Parsing.** Rows come from pdfplumber tables. Cells that run onto the next page are joined back to their entry. The serial numbers in every schedule are contiguous, with none missing, extra or duplicated: I = 1-516, II = 1-640, III = 1-13, IV = 1-15, V = 1-3, VI = 1-2, VII = 1-6, and 10/2025 = 1-172.

**Text check.** For every one of 1,361 original entries, the words in the description were compared with the words between serial N and serial N+1 in the PDF text. The check found no invented words and no lost text. There were two expected differences: List 1 is not part of the S1-516 description, and the S2-623 HSN cell contains the word "and".

**Amendments.** Each one is written into `parse_rates.py` and checked against the amendment PDF's own text. If a check fails, the build stops.

1. **19/2025** (in force 1 Feb 2026):
   - Inserted Schedule II S.No. 4A: Biris, 2403 19 21 and 2403 19 29, at 18%.
   - Inserted Schedule III S.No. 14-19 at 40%: pan masala 21069020, and tobacco 2401, 2402, 2403 (excluding biris), 240411 and 240419.
   - **Omitted Schedule VII (14%) entirely.** So the "14% vs 28%" question no longer arises: there is no 28% bracket now. The omitted entries are kept in `parse_audit.json`.
2. **Corrigendum of 18.09.2025:** the list in Sch I S.No. 292 (boards) is relabelled from b.-i. to a.-h.
3. **01/2026** (in force 1 May 2026), column (2) replaced as follows:
   - Sch I 150 (fruit-juice drinks): 2202 99 20 becomes 2202 99 21 and 2202 99 29.
   - Sch I 151 (milk beverages): 2202 99 30 becomes 22029931 and 22029939.
   - Sch III 2: becomes 2202 91 00, 2202 99 91 and 2202 99 99.
   - Sch III 3 (caffeinated): becomes 2202 99 91 and 2202 99 99.

   The amended rows show the change in `amended_by`.

**HSN handling.**
- Lists such as "a, b or c" become one row per code.
- Ranges such as "5208 to 5212" are expanded, and the expansion is noted.
- "Any chapter" becomes `hsn_prefix=ANY`.
- "(other than ...)" or "[Except ...]" in column (2) is moved into `condition_note` as "HSN excludes".
- One source cell has no comma ("2711 12 00 2711 13 00", Sch I 209). It is split wherever a new 4-digit group starts.
- Two exempt entries (EX-168 lottery, EX-169 government supplies) have "-" in column (2). Their `hsn_prefix` is blank.

## Row counts

The CSV has 1,570 rows covering 1,372 entries.

| Schedule | Total rate | Entries | Rows |
|---|---|---|---|
| I | 5% | 516 | 614 |
| II | 18% | 641 (incl. 4A) | 691 |
| III | 40% | 19 | 24 |
| IV | 3% | 15 | 15 |
| V | 0.25% | 3 | 3 |
| VI | 1.5% | 2 | 2 |
| Exempt (10/2025) | 0% | 172 | 217 |
| 14/2025 bricks | 12% | 4 | 4 |

## Guidance for consumers

**Two fields beyond the spec.** `has_condition` is set from the entry's own text: packaging or branding, "other than" and exclusions, value or size thresholds, end use or supplier, and "any chapter". There is also an extra column, `competing_entries`. It lists every entry at a different rate whose prefix is equal to this row's prefix, a prefix of it, or an extension of it.

**Rows flagged only because of an overlap (301 rows).** Some rows have no condition in their own text but are still marked `has_condition=true`. That happens when a broader or equal-length *conditional* entry competes with them. Example: 6403 is 18% in Sch II S.204, but Chapter 64 footwear of up to Rs 2500 a pair is 5% in Sch I S.392. Picking the most specific prefix alone would wrongly give 18% for cheap shoes. **The rule: if `has_condition` is true, or `competing_entries` lists a conditional entry, show the alternatives. Do not resolve them silently.**

**Residual rate.** Sch II S.639 ("Any Chapter: goods not specified in Schedule I, III, IV, V, VI or VII") makes 18% the default.

**Definition of "pre-packaged and labelled".** It is defined in the Explanation to 9/2025: retail packages of 25 kg or 25 litres or less that must carry Legal Metrology declarations.

## Known gaps

- **List 1 (assistive devices for the disabled, Sch I S.478) is not expanded into rows.** The entry refers to it. The same goes for Annexure I (drugs) and Annexure II (musical instruments) of 10/2025, which entries EX-113 and EX-161 refer to.
- **Conditional and concessional notifications outside 9/2025 and 10/2025 are not merged:** 11/2025 (3/2017), 12/2025, 13/2025, 15-17/2025 and 18/2025. The 14/2025 bricks rows are included and flagged. Note that 9/2025 also lists headings 6815, 6901, 6904 and 6905, for other goods.
- **Compensation cess:**
  - 02/2025 set the cess to Nil from 22.09.2025 for cess S.Nos 2, 3, 4, 4A, 4B, 39-42, 47, 48 and 50-55.
  - 03/2025 set it to Nil from 01.02.2026 for S.Nos 1, 1A, and 5-38 including their A-E variants.
  - I did not download the principal 1/2017-Compensation Cess schedule, so I could not confirm that every serial is now Nil. S.Nos 43-46, 49 and 56 onward are unchecked.
  - No cess column is included.
- **Text artefacts in the descriptions.** A few descriptions carry text-layer artefacts from the PDF, for example "combustio n" in Sch III S.7. Wrapped lines were joined with a space, and a hyphen at the end of a line is kept, as in "pre-packaged".

## Spot checks

Each was confirmed against the line in `raw/notifications_text/`.

| # | Item | Entry | Rate | Note |
|---|---|---|---|---|
| 1 | Biscuits 1905 | 9/2025-S1-123 | 5% | Text: "Pastry, cakes, biscuits and other bakers' wares". Bread is nil (EX-100) |
| 2 | Soap 3401 | S1-251 | 5% | Toilet soap bars. Other soap is 18% (S2-66) |
| 3 | Ghee 0405 | S1-7 | 5% | "Butter ... including ghee; dairy spreads" |
| 4 | Toothpaste 3306 | S1-246 | 5% | "Toothpaste". Other oral hygiene goods are 18% (S2-63) |
| 5 | Cement 2523 | S2-9 | 18% | Portland, slag and similar cements |
| 6 | Small cars 8703 | S2-534 | 18% | Petrol/LPG/CNG, up to 1200cc and 4000mm (870321/870322). Diesel up to 1500cc is S2-535. Other 8703 is 40% (S3-5) |
| 7 | Pan masala 21069020 | S3-14 (via 19/2025) | 40% | Was 28% (Sch VII) until 31.01.2026 |
| 8 | Unbranded rice 1006 | EX-67 | 0% | "Rice, other than pre-packaged and labelled". Pre-packaged rice is 5% (S1-48) |
| 9 | Cotton fabric 5208 | S1-334 | 5% | "5208 to 5212 Woven fabrics of cotton" |
| 10 | Footwear 6403/6404 | S1-392 vs S2-204/205 | 5% / 18% | 5% applies at sale value up to Rs 2500 a pair. Flagged |
| 11 | Garments 61/62 | S1-388/389 vs S2-197/198 | 5% / 18% | Rs 2500 per piece threshold |
| 12 | LPG 2711 | S1-208/209 | 5% | Domestic and NDEC supply only. Other 2711 is 18% (S2-30) |
| 13 | Mobile phones 8517 | S2-490 | 18% | "All goods" |
| 14 | Tea 0902 | S1-34 | 5% | Unprocessed green leaves are nil (EX-58) |
| 15 | Namkeen 2106/21069099 | S1-143 (210690), S1-145 (2106, excluding 21069020) | 5% | "whether or not pre-packaged and labelled" |
