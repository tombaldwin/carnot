# Fact-check: "The same limit, one level down" and "Crystallise the work"

File reviewed: `/Users/tom/git/web-heat-death/src/writing/heat-death-of-the-codebase/index.njk`
Sections: `<h2>The same limit, one level down</h2>` to `<h2>The model</h2>` (lines 72–86), `<h2>Crystallise the work</h2>` to `<h2>Limits</h2>` (lines 268–284), and reference entries at lines 284–289 and 307.
Date: 2026-09-27. Nothing in the file was edited.

Verdicts: VERIFIED / WRONG / OVERSTATED / UNVERIFIABLE. Sources are primary (paper abstracts or full text via PubMed, Crossref, arXiv, JBC, nobelprize.org) unless marked otherwise.

## Summary of things that must change

| # | Line | Problem | Severity |
|---|------|---------|----------|
| 1 | 289 | Walter & Ron DOI `10.1126/science.1209126` resolves to a different paper (Gardner & Walter 2011). Correct DOI is `10.1126/science.1209038`. | WRONG |
| 2 | 284 | Crystals are listed as dissipative structures. Prigogine's Nobel lecture explicitly names crystals as *equilibrium* structures, the contrast class. | WRONG |
| 3 | 284 | "heat death is the fate of a closed system, one that nothing flows into" — thermodynamics: closed systems exchange energy; the term is *isolated* system. | WRONG (terminology) |
| 4 | 284 (refs) | METR title. arXiv v1 (Mar 2025) was "Measuring AI Ability to Complete Long Tasks"; current v3/v4 (2026) is "Measuring AI Ability to Complete Long **Software** Tasks". | OVERSTATED / stale |
| 5 | 81 | DNA fidelity figures attributed to Kunkel 2004 do not match what Kunkel 2004 says (selectivity 10⁻⁴–10⁻⁶, not 10⁻⁴–10⁻⁵; he gives 10⁻⁷–10⁻⁸ *before* mismatch repair and no final figure). The 10⁻⁹–10⁻¹⁰ figure is correct but needs a different source (Drake et al. 1998). | OVERSTATED / misattributed |
| 6 | 80 | "Bennett later showed that fewer errors fundamentally cost more energy" — Bennett's result is narrower: minimum error is reached only in the limit of infinite dissipation, but most of the benefit comes at 0.1–1 kT/step. | OVERSTATED |
| 7 | 282 | "Evolution is a slow, wasteful, random search" — mutation is random; selection is not. | OVERSTATED |
| 8 | 74 | Stroebl et al. paraphrase: "plateaus at the verifier's own reliability" — the paper's bound is the verifier's false-positive rate. | OVERSTATED (imprecise) |
| 9 | 85 | UPR senses misfolded proteins in the ER only, and does more than throttle production. | OVERSTATED (minor) |

Everything else in the two sections checks out, with small wording notes below.

---

## Section 1: "The same limit, one level down" (lines 72–86)

### Line 73 — METR: success falls with task length

**Claim:** "each step has some chance of going wrong, so the chance of finishing falls away with length, which is the pattern behind METR's measurements of how long a task agents can complete."

**Verdict: VERIFIED (claim), OVERSTATED (attribution of mechanism).**
arXiv 2503.14499 (HTML, current version): "There is a negative correlation between the time it takes a human baseliner to complete a task and the average success rate (across all models) on the task", and "Model success rates are negatively correlated with how much time it takes a human to complete the task." METR fits a logistic curve in log(human time); they do not themselves attribute the decline to a constant per-step failure probability. "The pattern behind" implies METR's data are explained by the per-step model; they are consistent with it, which is weaker.

**Suggested replacement:** "…so the chance of finishing falls away with length, which is consistent with METR's measurements of how long a task agents can complete."

### Reference line 284 — Kwa et al. (METR) 2025

**Verdict: VERIFIED for authors and year; title is stale.**
Crossref/arXiv: lead author Thomas Kwa; 26 authors; METR. v1 submitted 18 March 2025 under the title "Measuring AI Ability to Complete Long Tasks". v3 (25 Feb 2026) and v4 (10 Jul 2026) carry the title "Measuring AI Ability to Complete Long Software Tasks" (confirmed from both the abs page and the HTML full text). The URL resolves.

**Suggested replacement (cite the current version):**
`<li>Kwa, T. et al. (METR). <a href="https://arxiv.org/abs/2503.14499">Measuring AI ability to complete long software tasks</a>. 2025.</li>`
If you prefer to keep the original title, link to v1 explicitly: `https://arxiv.org/abs/2503.14499v1`.

### Line 74 — Stroebl, Kapoor, Narayanan

**Claim:** "when a verifier sometimes passes wrong answers, the accuracy you reach by generating more and checking plateaus at the verifier's own reliability, however much compute you spend."

**Verdict: OVERSTATED (imprecise).** arXiv 2411.17501 abstract: resampling "is fundamentally limited when verifiers are imperfect and have a non-zero probability of producing false positives. Resampling cannot decrease this probability, so it imposes an upper bound to the accuracy of resampling-based inference scaling, regardless of compute budget." Also: "No amount of inference scaling of weaker models can enable them to match the single-sample accuracy of a sufficiently strong model." The bound is set by the false-positive rate specifically (a verifier can be "unreliable" by rejecting correct answers without capping accuracy; only passing wrong ones does). "Plateaus at the verifier's own reliability" blurs this. Note the same paraphrase appears at line 204 ("an imperfect verifier caps the accuracy of resampling however much compute you spend"), which is accurate as written.

**Suggested replacement:** "Stroebl, Kapoor and Narayanan showed that when a verifier sometimes passes wrong answers, generating more candidates and checking them cannot push accuracy past a ceiling set by how often the verifier waves a wrong answer through, however much compute you spend."

### Line 74 — "A model reviewing a model's work also tends to share its blind spots, so their errors do not cancel out."

**Verdict: VERIFIED as a "tends to" claim; add a citation.**
- Kim, Garg, Peng and Garg, "Correlated Errors in Large Language Models", ICML 2025 (arXiv 2506.07962): across 350+ models, "on one leaderboard dataset, models agree 60% of the time when both models err"; larger and more accurate models have more correlated errors even across architectures and providers; effects demonstrated in LLM-as-judge evaluation.
- Panickssery, Bowman and Feng, "LLM Evaluators Recognize and Favor Their Own Generations", NeurIPS 2024: self-preference bias, linearly correlated with self-recognition ability.
"Tends to" is correctly hedged. "Do not cancel out" is slightly absolute; the evidence shows partial correlation, not identity.

**Suggested tweak (optional):** "…so their errors only partly cancel." Add to References: `<li>Kim, E., Garg, A., Peng, K., Garg, N. <a href="https://arxiv.org/abs/2506.07962">Correlated errors in large language models</a>. ICML, 2025.</li>`

### Line 75 — types, tests, proofs "almost never wave a wrong change through"

**Verdict: VERIFIED as stated** (soundness within what is specified; the sentence already limits the claim with "Inside what they cover"). No change.

### Line 78 — "Living cells have faced this problem for about four billion years"

**Verdict: VERIFIED** (LUCA and earliest life ~3.5–4.0 Ga; standard).

### Line 80 — Kinetic proofreading: Hopfield 1974, Ninio 1975

**Claim:** enzymes "tell right building blocks from wrong ones far better than the energy difference between them should allow. Hopfield and Ninio showed in 1974 and 1975 how: they spend energy on extra, irreversible checking steps, called kinetic proofreading."

**Verdict: VERIFIED.**
- Hopfield, PNAS 71:4135–4139 (Oct 1974), abstract: specificity "can be increased above the level available from free energy differences in intermediates or kinetic barriers by a process defined here as kinetic proofreading… when the reaction is strongly but nonspecifically driven, e.g., by phosphate hydrolysis. Protein synthesis, amino acid recognition, and DNA replication, all exhibit the features of this model."
- Ninio, Biochimie 57:587–595 (1975), "Kinetic amplification of enzyme discrimination": "Certain mechanisms of reaction, involving a delay in one of the steps act as kinetic amplifiers of molecular discriminations. The relationship between our scheme for a delayed reaction and Hopfield's scheme is discussed." Attribution to both, in that order and those years, is correct. (Ninio's paper cites Hopfield, so do not add the word "independently" without qualification; the text does not, which is fine.)
- Irreversibility: Hopfield's mechanism requires a strongly driven (effectively irreversible) step; Murugan, Huse and Leibler (PNAS 2012) summarise it as mechanisms that "disrupt and reset the reaction to undo errors at the cost of increased time of reaction and free energy expenditure."

**Wording a physicist would tighten:** "energy difference" → "binding-energy difference" or "free-energy difference". Suggested: "…far better than the difference in binding energy between them should allow."

### Line 80 — Bennett 1979

**Claim:** "Bennett later showed that fewer errors fundamentally cost more energy."

**Verdict: OVERSTATED.** Bennett, BioSystems 11:85–91 (Aug 1979), abstract: "Chemical proofreading systems… achieve minimum error probability (equal to the product of the error probabilities of the writing and proofreading stages) only in the limit of infinite energy dissipation. However, a considerable degree of proofreading can be obtained in less strongly driven systems, dissipation only 0.1–1 kT/step." So: (a) the trade-off is real and monotone within the proofreading scheme he analysed; (b) it has a floor (writing error × proofreading error), not zero; (c) he stresses that most of the benefit is cheap. "Fundamentally" suggests a general law of nature; Bennett showed it for a specific kinetic scheme, and the sentence hides the "cheap most of the way" half of the result.

**Suggested replacement:** "Bennett later showed the trade-off has no free end: the lowest error rate a proofreading scheme can reach is approached only as the energy it burns goes to infinity."
(Or, shorter: "Bennett later showed that pushing the error rate down costs ever more energy, with the minimum reached only at infinite dissipation.")

### Line 81 — DNA replication fidelity numbers (Kunkel 2004)

**Claim:** "Copying DNA makes roughly one error in 10⁴ to 10⁵ bases at first pass. Proofreading removes most of those, and mismatch repair most of the rest, for roughly one in 10⁹ to 10¹⁰."

**Verdict: OVERSTATED / misattributed.** Kunkel, JBC 279:16895–16898 (2004), full text (read at jbc.org):
- "high fidelity typically results from 10⁴ to 10⁶-fold polymerase selectivity for inserting correct rather than incorrect nucleotides, followed by excision of 90–99.9% of base-base mismatches by exonucleases" — so base selection is 10⁻⁴ to 10⁻⁶, not 10⁻⁴ to 10⁻⁵; proofreading is a 10- to 1000-fold improvement.
- "the base substitution error rate of the replication machinery in vivo [in the absence of mismatch repair] is in the range of 10⁻⁷ to 10⁻⁸."
- Kunkel 2004 gives no overall post-mismatch-repair figure. The 10⁻⁹–10⁻¹⁰ per base per replication figure is right but comes from elsewhere: Drake, Charlesworth, Charlesworth and Crow, Genetics 148:1667 (1998): DNA-based microbes mutate at ~1/300 per genome per replication, i.e. ~5×10⁻¹⁰ per bp for E. coli's 4.6 Mb genome. Mismatch repair contributing a further 100–1000-fold is the standard figure (Kunkel & Erie, Annu Rev Biochem 2005; Alberts, Molecular Biology of the Cell).

**Suggested replacement:** "Copying DNA makes roughly one error in 10⁴ to 10⁶ bases from base selection alone. Proofreading removes 90 to 99.9 per cent of those, and mismatch repair a further 99 per cent or more, for roughly one in 10⁹ to 10¹⁰ per base per replication."
Add to References: `<li>Drake, J. W., Charlesworth, B., Charlesworth, D., Crow, J. F. <a href="https://doi.org/10.1093/genetics/148.4.1667">Rates of spontaneous mutation</a>. Genetics, 1998.</li>` (Optionally Kunkel & Erie, "DNA mismatch repair", Annu Rev Biochem 2005, doi 10.1146/annurev.biochem.74.082803.133243.)

### Line 81 — "That only works because the layers fail independently."

**Verdict: OVERSTATED (minor).** Multiplying the three factors is the standard textbook estimate, and the mechanisms are distinct (geometric selection in the polymerase active site; exonucleolytic excision of frayed mismatched termini; post-replicative strand-directed repair). But they are not strictly independent: efficiencies of both proofreading and mismatch repair depend on mismatch type and sequence context (Kunkel 2004 notes proofreading is poor in long repeats; Kunkel & Erie 2005 note mismatch-type dependence in MMR), and mismatch repair only sees what proofreading missed. A biologist would accept "largely independent" or "by different mechanisms".

**Suggested replacement:** "That only works because each layer catches errors by a different mechanism, so what one misses the next mostly does not. Two models reviewing the same way do not have that property."

### Line 82 — proteins: error rate and turnover

**Claims:** "Proteins are replaced within hours to days, and are built with errors around a million times more often."

**Verdict: VERIFIED with one hedge.**
- Translation error rate 10⁻³–10⁻⁴ per codon: BioNumbers (book.bionumbers.org, "What is the error rate in transcription and translation?"): "10⁻⁴–10⁻³" per codon; Drummond & Wilke 2009 (Nat Rev Genet): 10⁻⁴–10⁻³ per residue. Ratio to DNA (10⁻⁹–10⁻¹⁰): 10⁵ to 10⁷, so "around a million" is a fair geometric middle.
- Turnover: Schwanhäusser et al., Nature 473:337 (2011): median protein half-life 46 h vs 9 h for mRNA in mouse NIH3T3 fibroblasts (figure reported in the paper; confirmed via BioNumbers BNID 106377 and H1 Connect summary). BioNumbers also gives 1–2 days for HeLa. So "hours to days" is right for a typical mammalian protein. Caveats a biologist would raise: in bacteria most proteins are not degraded at all but diluted by growth (doubling ~20–60 min), and some proteins (lens crystallins, collagen, nuclear pore components) last years to a lifetime.

**Suggested replacement:** "Most proteins are replaced within hours to days, and are built with errors around a million times more often."

### Line 83 — crystals: speed and defects

**Claim:** "grow them fast and defects freeze in; grow them slowly and misplaced atoms have time to leave."

**Verdict: VERIFIED as a qualitative statement.** McPherson & Kuznetsov, Acta Cryst F 70:384 (2014): nucleation and growth kinetics "are all supersaturation-dependent"; defect structure and impurity incorporation depend on growth conditions; near equilibrium, attachment is reversible so wrongly attached units detach before being buried. This is textbook crystal growth (Burton–Cabrera–Frank; kinetic roughening at high supersaturation) and is also the analogy Bennett himself uses for proofreading. A physicist would accept it. "Atoms" is fine for atomic crystals; for protein crystals it is "molecules", but the sentence is generic.

### Line 84 — unfolded protein response

**Claim:** "When misfolded proteins pile up faster than a cell's quality control can deal with them, the unfolded protein response throttles protein production until it catches up."

**Verdict: VERIFIED mechanism, OVERSTATED scope (minor).**
- Translation attenuation via PERK → eIF2α phosphorylation: Harding, Zhang & Ron, Nature 397:271 (1999) ("Protein translation and folding are coupled by an endoplasmic-reticulum-resident kinase"); Harding et al., Mol Cell 5:897 (2000) ("Perk is essential for translational regulation and cell survival during the unfolded protein response"): PERK-null cells show "abnormally elevated protein synthesis and higher levels of ER stress". Walter & Ron 2011 (Science 334:1081) covers the three branches (IRE1, PERK, ATF6) and PERK's attenuation of translation initiation to reduce ER folding load. The citation is appropriate.
- Scope: the UPR senses unfolded proteins in the endoplasmic reticulum lumen only (Walter & Ron abstract: "The ER responds to the burden of unfolded proteins in its lumen (ER stress)…"); cytosolic misfolding triggers the separate heat-shock response. And the UPR does not only throttle: it also expands folding capacity (chaperones, ER membrane) and degrades ER-bound mRNAs, and triggers apoptosis if stress persists.

**Suggested replacement:** "When misfolded proteins pile up in a cell's protein-folding compartment faster than its quality control can deal with them, the unfolded protein response throttles new protein synthesis and expands folding capacity until it catches up."

### Reference line 289 — Walter & Ron 2011

**Verdict: WRONG DOI.** `10.1126/science.1209126` resolves (Crossref) to Gardner & Walter, "Unfolded Proteins Are Ire1-Activating Ligands That Directly Induce the Unfolded Protein Response", Science 333:1891 (30 Sep 2011). The cited review is Walter & Ron, "The Unfolded Protein Response: From Stress Pathway to Homeostatic Regulation", Science 334(6059):1081–1086 (25 Nov 2011), DOI `10.1126/science.1209038` (Crossref and PubMed PMID 22116877 agree).

**Replacement:**
`<li>Walter, P., Ron, D. <a href="https://doi.org/10.1126/science.1209038">The unfolded protein response: from stress pathway to homeostatic regulation</a>. Science, 2011.</li>`

### Reference lines 285–288 — DOI checks

| Ref | DOI | Crossref resolves to | Verdict |
|-----|-----|----------------------|---------|
| Hopfield 1974 | 10.1073/pnas.71.10.4135 | Hopfield, "Kinetic Proofreading: A New Mechanism for Reducing Errors in Biosynthetic Processes Requiring High Specificity", PNAS 71(10):4135–4139, Oct 1974 | VERIFIED |
| Ninio 1975 | 10.1016/S0300-9084(75)80139-8 | Ninio, "Kinetic amplification of enzyme discrimination", Biochimie 57(5):587–595, 1975 | VERIFIED |
| Bennett 1979 | 10.1016/0303-2647(79)90003-0 | Bennett, "Dissipation-error tradeoff in proofreading", BioSystems 11(2–3):85–91, Aug 1979 | VERIFIED |
| Kunkel 2004 | 10.1074/jbc.R400006200 | Kunkel, "DNA Replication Fidelity", J Biol Chem 279(17):16895–16898, Apr 2004 | VERIFIED |
| Walter & Ron 2011 | 10.1126/science.1209126 | Gardner & Walter 2011 (wrong paper) | WRONG, see above |
| Kwa et al. 2025 | arxiv.org/abs/2503.14499 | Kwa et al., METR | VERIFIED (title stale) |

(PNAS and Science return 403 to curl but the DOI redirects go to the correct article URLs; Crossref metadata confirms the targets.)

---

## Section 2: "Crystallise the work" (lines 268–284) and Prigogine reference (line 307)

### Line 278 — compiler analogy

**Claim:** "Nobody reviews its output line by line, because the checking was done once, very thoroughly, and the result is reused millions of times."

**Verdict: VERIFIED as an analogy; one wording nit.** Compilers are not checked "once": they carry large regression suites and are re-verified on every release, and they do have bugs (Yang, Chen, Eide & Regehr, PLDI 2011, found 325 bugs in mainstream C compilers with Csmith; Thompson's 1984 "Reflections on Trusting Trust" is the classic caveat). The point that verification cost is amortised across uses stands. Since the paragraph's third caveat (line 283) already says tools need maintenance, the "once" is defensible, but "done once and kept up" would pre-empt the objection.

**Suggested tweak (optional):** "because the checking was done up front, very thoroughly, is kept up by someone else, and the result is reused millions of times."

### Line 282 — "Evolution is a slow, wasteful, random search"

**Verdict: OVERSTATED.** Standard biologist's objection: mutation (variation) is random with respect to fitness; natural selection is not random. UC Berkeley "Understanding Evolution" misconceptions page: "To say that evolution happens 'by chance' ignores half of the picture"; mutation random, "Selection favored variants that were better able to survive and reproduce". "Stored in DNA and reused billions of times as reliable machinery" is fine (ribosomes, ATP synthase, DNA polymerase are conserved across all life).

**Suggested replacement:** "Evolution is a slow, wasteful search, random variation filtered by non-random selection, but what it finds is stored in DNA and reused billions of times as reliable machinery."

### Line 283 — "Lehman's law of continuing change"

**Verdict: VERIFIED.** Lehman, "Laws of Software Evolution Revisited", EWSPT 1996 (PDF at the cited URL, read in full): Law I is "Continuing Change: An E-type program that is used must be continually adapted else it becomes progressively less satisfactory." (Law II, "Increasing Complexity", is the one Lehman says "may be an analogue of the second law of thermodynamics or an instance of it", which matches line 65 of the paper, outside this review's scope but also correct.)

### Line 284 — heat death, closed vs isolated systems

**Claim:** "In physics, heat death is the fate of a closed system, one that nothing flows into."

**Verdict: WRONG (terminology).** In thermodynamics a *closed* system exchanges energy but not matter; an *isolated* system exchanges neither. The second-law statement underlying heat death ("entropy tends to increase") is for isolated systems (Wikipedia's own text on heat death uses "isolated system"; "closed" appears there only inside a critic's quotation). The gloss "one that nothing flows into" is the definition of an isolated system, so only the label is wrong. A physicist reading "closed system" with that gloss will object.

**Suggested replacement:** "In physics, heat death is the fate of an isolated system, one that nothing flows into or out of."

### Line 284 — crystals as dissipative structures

**Claim:** "Living things, crystals and even hurricanes build and keep their structure because energy flows through them and they export the disorder; Prigogine called these dissipative structures."

**Verdict: WRONG for crystals; VERIFIED for living things and hurricanes.** Prigogine's Nobel lecture (PDF, nobelprize.org, "Time, Structure and Fluctuations", 8 December 1977) sets crystals up as the explicit contrast class: "from the macroscopic point of view classical thermodynamics has largely clarified the concept of equilibrium structures such as crystals. Thermodynamic equilibrium may be characterized by the minimum of the Helmholtz free energy… Are most types of 'organisations' around us of this nature? … Obviously in a town, in a living system, we have a quite different type of functional order… Irreversible processes may lead to a new type of dynamic states of matter which I have called 'dissipative structures'." A crystal at rest is an equilibrium structure that holds its order with no throughput of energy. (Crystal *growth* is driven and releases latent heat, but the crystal is not maintained by a flow.) Living organisms, hurricanes/cyclones and Bénard convection are the standard examples of dissipative structures (Wikipedia, "Dissipative system", as pointer).

This matters for the paper's own metaphor: a tool ("crystallised work", line 282) is precisely the *equilibrium* kind of structure, order that stays put without further flow, while the evolving codebase is the dissipative kind that needs verification flowing through it. Separating the two makes the argument sharper rather than weaker.

**Suggested replacement:** "Living things and even hurricanes build and keep their structure because energy flows through them and they export the disorder; Prigogine called these dissipative structures, and contrasted them with equilibrium structures such as crystals, which hold their order without any further flow. A codebase under change is the first kind; a finished tool is the second. What flows in to keep the codebase ordered is verification: reviewers, tests, checkers, and the tools that stay checked."
(Then continue "Keep paying the demon and the structure holds…" unchanged.)

"They export the disorder" is fine shorthand for entropy export (Prigogine's negative entropy flow term); a physicist might prefer "export the entropy", but "disorder" is acceptable in prose.

### Reference line 307 — Prigogine

**Verdict: VERIFIED.** Nobel lecture title "Time, Structure and Fluctuations", delivered 8 December 1977; the cited URL `https://www.nobelprize.org/prizes/chemistry/1977/prigogine/lecture/` loads the lecture page (browser title "Ilya Prigogine – Nobel Lecture – NobelPrize.org"); the PDF at nobelprize.org/uploads/2018/06/prigogine-lecture.pdf carries the same title. Entry is correct as written.

---

## Wording notes a biologist or physicist would raise (not errors)

- Line 80: "energy difference" → "binding-energy difference" (Hopfield's phrase is "free energy differences").
- Line 80: "irreversible checking steps" is accurate as shorthand; strictly the driven step is *nearly* irreversible because GTP/ATP hydrolysis is held far from equilibrium.
- Line 81: use "base selection" or "nucleotide selection" rather than "at first pass", which is what the replacement above does.
- Line 82: prefix "Most" to the protein-turnover sentence (bacteria dilute rather than degrade; some proteins last a lifetime).
- Line 84: UPR is ER-specific; see replacement above.
- Line 284: "closed" → "isolated"; crystals are not dissipative structures.

## Sources consulted

- Hopfield 1974: PubMed 4530290 abstract; Crossref 10.1073/pnas.71.10.4135.
- Ninio 1975: PubMed 1182215 abstract; Crossref.
- Bennett 1979: PubMed 497372 abstract; Crossref.
- Kunkel 2004: full text at jbc.org/article/S0021-9258(19)75503-3/fulltext; Crossref.
- Kunkel & Erie 2005: PubMed 15952900. Drake et al. 1998: PubMed 9560386 abstract.
- Schwanhäusser et al. 2011: PubMed 21593866; BioNumbers BNID 106377.
- BioNumbers, "What is the error rate in transcription and translation?"; "How fast do proteasomes degrade proteins?".
- Harding, Zhang & Ron 1999 Nature; Harding et al. 2000 Mol Cell (PubMed 10882126).
- Walter & Ron 2011: PubMed 22116877; Crossref 10.1126/science.1209038 vs 10.1126/science.1209126.
- Murugan, Huse & Leibler 2012: PubMed 22786930.
- McPherson & Kuznetsov 2014: PubMed 24699728.
- Kwa et al.: arxiv.org/abs/2503.14499 (abs, v1, HTML).
- Stroebl, Kapoor & Narayanan: arxiv.org/abs/2411.17501.
- Kim, Garg, Peng & Garg 2025: arxiv.org/abs/2506.07962. Panickssery, Bowman & Feng 2024: NeurIPS proceedings.
- Prigogine 1977: nobelprize.org lecture page and PDF.
- Lehman 1996: cs.kent.edu PDF (full text).
- UC Berkeley Understanding Evolution, misconceptions page.
- Wikipedia as pointer only: Kinetic proofreading, Dissipative system, Heat death of the universe, Crystal growth.
