# Graph-Me-Maybe Benchmarks

> Surveyed: 2026-09-22
>
> This collects public benchmarks that let us compare our pipeline with others. It lists them
> in two ways: **§1 by fame** (made by large companies or used to evaluate their own models →
> well-known papers and conferences), and **§2 which stage of our pipeline each one measures**.
> The same sets appear in both.
> For what to run first, see [`roadmap.md`](roadmap.md) §0.

## 1. By fame

### Tier S — reported by every frontier model

Document benchmarks that big-tech model cards and technical reports carry by default (the Qwen2.5-VL technical report, the Gemini and GPT-4o comparison tables, and so on).

| # | Benchmark | Nature | Fit for us |
|---|---|---|---|
| 1 | **DocVQA** | The de facto standard for document image QA | ★★☆ single page |
| 2 | **ChartQA** | Chart QA | ★★★ chart nodes |
| 3 | **InfographicVQA** | Complex, irregular layouts | ★★☆ |
| 4 | **AI2D / TextVQA** | Diagrams and scene text | ★☆☆ |

### Tier A — made by well-known companies

| # | Benchmark | Made by | Content | Fit for us |
|---|---|---|---|---|
| 5 | [CRAG](https://www.aicrowd.com/challenges/meta-comprehensive-rag-benchmark-kdd-cup-2024) | **Meta Reality Labs** (+HKUST) | Official KDD Cup 2024 task. Comprehensive RAG evaluation over 5 domains and 8 question types | ★★☆ |
| 6 | [FRAMES](https://huggingface.co/datasets/google/frames-benchmark) | **Google DeepMind** | Factuality, retrieval and multi-constraint reasoning in one | ★★☆ |
| 7 | [UniDoc-Bench](https://github.com/SalesforceAIResearch/UniDoc-Bench) | **Salesforce AI Research** | 70k pages of real PDFs, 8 domains, 1,600 QA. Compares the **4 paradigms** text-only, image-only, fused and joint under the same protocol | ★★★ checks design choices |
| 8 | [VisDoMBench](https://aclanthology.org/2025.naacl-long.310/) | **Adobe Research** (NAACL'25) | Multiple documents + tables, charts and slides | ★★★ |
| 9 | [M3DocVQA](https://github.com/bloomberg/m3docrag) | **Bloomberg** | Open-domain, 3,000+ PDFs / 40,000+ pages | ★★☆ |
| 10 | REAL-MM-RAG | **IBM Research** | Checks semantic understanding with rephrasing difficulty tiers | ★★☆ |
| 11 | [DocLayNet](https://github.com/DS4SD/DocLayNet) / [PubTabNet](https://github.com/ibm-aur-nlp/PubTabNet) | **IBM Research** | Layout detection / table structure (500k+) | ★★★ structure layer |
| 12 | [SlideVQA](https://github.com/nttmdlab-nlp/SlideVQA) | **NTT** | Slide decks of ~20 pages | ★★★ closest to PPTX |
| 13 | [SDS KoPub VDR](https://arxiv.org/pdf/2511.04910) | **Samsung SDS** | Page retrieval over Korean public documents | ★★★ Korean |
| 14 | [graphrag-benchmarking-datasets](https://github.com/microsoft/graphrag-benchmarking-datasets) | **Microsoft** | Official GraphRAG benchmarking sets | ★★☆ |
| 15 | [ViDoRe](https://github.com/illuin-tech/vidore-benchmark) | Illuin Technology (ColPali, ICLR'25) | Page-level visual retrieval, runs an HF leaderboard | ★★★ retrieval |
| 16 | [FinanceBench](https://github.com/patronus-ai/financebench) | Patronus AI | QA over financial reports | ★★☆ |

### Tier B — from well-known papers and conferences

| # | Benchmark | Source | Content | Fit for us |
|---|---|---|---|---|
| 17 | [MMLongBench-Doc](https://proceedings.neurips.cc/paper_files/paper/2024/file/ae0e43289bffea0c1fa34633fc608e92-Paper-Datasets_and_Benchmarks_Track.pdf) | NeurIPS'24 D&B | 130 documents, up to 468 pages, expert annotation of **cross-page** questions | ★★★ cross-page hierarchy |
| 18 | [OmniDocBench](https://github.com/opendatalab/OmniDocBench) | CVPR'25 (OpenDataLab) | 1,651 PDF pages, 28 block types + 4 span types, **reading-order annotation**, tables annotated in both LaTeX and HTML. NED and TEDS | ★★★ structure-layer standard |
| 19 | [GraphRAG-Bench](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark) | ICLR'26 (PolyU) | Fact retrieval → multi-hop reasoning → contextual summarization → creative generation. Evaluates construction, retrieval and generation **across the whole pipeline**, separately | ★★★ decides whether to adopt a graph |
| 20 | [HotpotQA](https://hotpotqa.github.io/) | EMNLP'18 | The classic for multi-hop reasoning | ★★☆ |
| 21 | [MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG) | COLM'24 (HKUST) | 2,556 queries whose evidence spans 2 to 4 documents | ★★☆ |
| 22 | 2WikiMultihopQA / MuSiQue | COLING'20 / TACL'22 | Standard for multi-hop reasoning | ★★☆ |
| 23 | [DUDE](https://rrc.cvc.uab.es/?ch=23) | ICCV'23 competition | ~5,000 documents (5.7 pages on average), medical, legal, technical and financial | ★★☆ |
| 24 | MP-DocVQA | CVC Barcelona | Scanned multi-page, page-index reasoning | ★★☆ |
| 25 | [LongDocURL](https://arxiv.org/abs/2412.18424) | 2024-12 | ~30 pages on average, mixed headings, links and tables | ★★☆ |
| 26 | [MMESGBench](https://arxiv.org/pdf/2507.18932) | 2025 | ESG **corporate reports** | ★★★ similar document form |
| 27 | [TAT-DQA](https://nextplusplus.github.io/TAT-DQA/) | ACM MM (NUS) | Reasoning over financial documents with tables + text | ★★☆ |
| 28 | [MKG-RAG-Bench](https://arxiv.org/html/2606.26458v1) | 2026 | Dedicated to multimodal KG-RAG | ★★☆ |
| 29 | [DocBench](https://github.com/Anni-Zou/DocBench) / [M-LongDoc](https://arxiv.org/html/2411.06176v1) / [XL-DocBench](https://arxiv.org/html/2608.00036) / [SynthDocBench](https://arxiv.org/html/2607.10400v1) | 2024~2026 | Long and ultra-long variants | ★☆☆ |
| 30 | [SciCap](https://github.com/tingyaohsu/SciCap) | 2021~ | Figure caption generation | ★★★ evaluates caption nodes |

## 2. Which stage of our pipeline each one measures

### A. Document parsing and structure — extraction, rendering, territory claiming, hierarchy

| Benchmark | What it measures |
|---|---|
| **OmniDocBench** | The standard for structure-layer evaluation. Reading order and table TEDS. The Evidence Units paper also measured with it |
| DocLayNet | Layout region detection only |
| PubTabNet / FinTabNet / SciTSR | Table structure recognition (TEDS) — table node quality |
| ReadingBank | Reading order — `NEXT` and order judgment |
| [MPDocBench-Parse](https://arxiv.org/html/2605.22100v1), [Dr.DocBench](https://arxiv.org/html/2606.01393v1) | Multi-page, expert-level parsing. The latest comparison group |
| SciCap | Figure captions — evaluates `vlm_enriched_caption` |

### B. Visual document retrieval — the retrieval entry point

| Benchmark | What it measures |
|---|---|
| **SDS KoPub VDR** | Page retrieval over Korean public documents. The best page among several candidates per question — closest because the corpus is Korean |
| **ViDoRe** | Page-level retrieval, text / figure / infographic / table × medical, business, science, administration |
| MMDocIR | Retrieval by page + layout unit in long documents — corresponds to our node-level retrieval |
| REAL-MM-RAG | Rephrasing difficulty tiers — robustness of semantic understanding |

### C. Multi-page, multimodal document QA — end to end

| Benchmark | What it measures |
|---|---|
| **SlideVQA** | Slide decks. Closest to the PPTX path, and the set MegaRAG used as its main one |
| **MMLongBench-Doc** | Cross-page queries — a direct test for the page carry (open ancestors, `holds`, figure cut) |
| **UniDoc-Bench** | Compares text-only / image-only / fused under the same protocol — for us, which hold both captions and crops, this is a design decision right away |
| **VisDoMBench** | Multi-document queries — queries across documents linked by `REFERENCES` |
| LongDocURL · DUDE · MP-DocVQA · M3DocVQA | Long, scanned and open-domain variants |
| MMESGBench | Corporate report form |
| DocVQA · InfographicVQA · ChartQA · TAT-DQA | Partial abilities |

### D. GraphRAG itself

| Benchmark | What it measures |
|---|---|
| **GraphRAG-Bench** | Difficulty tiers (fact retrieval → multi-hop reasoning → contextual summarization → creative generation). The whole pipeline: construction, retrieval and generation |
| microsoft/graphrag-benchmarking-datasets | A collection of MS's official benchmarking datasets |
| MultiHop-RAG | Queries whose evidence spans 2 to 4 documents |
| HotpotQA / 2WikiMultihopQA / MuSiQue / NQ | Multi-hop reasoning standards (mostly Wikipedia-based) |
| UltraDomain (LightRAG), NarrativeQA, QuALITY | Domain and long documents |
| MKG-RAG-Bench | Dedicated to multimodal KG-RAG |
| CMEL (MMGraphRAG) | Cross-modal entity alignment |

## 3. Two reasons we cannot use them as they are

1. **The input format does not match.** Almost all of the benchmarks above are PDF- or image-based, but we
   render XLSX, PPTX and DOCX originals. Only the PDF family and SlideVQA can be used as they are, and the rest
   cannot measure our strengths (original coordinates and cell ranges).
2. **There is no public benchmark for internal technical documents.** No public set matches internal reports, and
   the closest in form are MMESGBench and TAT-DQA. In the end we need **our own gold set** — a local regression set
   that stores the sequential hierarchy results as gold is the starting point.

## 4. What to actually pick

Considering fame and fit together, it narrows to three.

1. **SlideVQA** (NTT) — the only well-known set that measures the PPTX path directly. It tests the slide hierarchy, `NEXT` and crops.
2. **MMLongBench-Doc** (NeurIPS'24) — cross-page queries over long PDFs. It checks the page-carry handling exactly.
3. **UniDoc-Bench** (Salesforce) — measures which is better among text-only / image-only / fused under the same protocol.
   It is the place to test the judgment that set the first round to text-only.

For a Korean corpus, add **SDS KoPub VDR** (Samsung SDS) as one more retrieval evaluation.

By stage, we split the use like this.

| Layer | Set |
|---|---|
| Structure | OmniDocBench (reading order, table TEDS) — applies directly to the PDF path only |
| Caption | Apply SciCap-style metrics to our captions |
| Retrieval | SDS KoPub VDR (Korean) + ViDoRe |
| E2E | SlideVQA + MMLongBench-Doc + UniDoc-Bench (borrow the protocol only) |
| Graph | Borrow the **task axes** of GraphRAG-Bench and build our own Q&A on our corpus |

## 5. Sources

[Qwen2.5-VL technical report](https://arxiv.org/pdf/2502.13923) ·
[OmniDocBench (CVPR'25)](https://openaccess.thecvf.com/content/CVPR2025/papers/Ouyang_OmniDocBench_Benchmarking_Diverse_PDF_Document_Parsing_with_Comprehensive_Annotations_CVPR_2025_paper.pdf) ·
[MPDocBench-Parse](https://arxiv.org/html/2605.22100v1) ·
[Dr.DocBench](https://arxiv.org/html/2606.01393v1) ·
[SDS KoPub VDR](https://arxiv.org/pdf/2511.04910) ·
[ColPali/ViDoRe (ICLR'25)](https://proceedings.iclr.cc/paper_files/paper/2025/file/99e9e141aafc314f76b0ca3dd66898b3-Paper-Conference.pdf) ·
[UniDoc-Bench](https://arxiv.org/abs/2510.03663) ·
[VisDoM (Adobe, NAACL'25)](https://aclanthology.org/2025.naacl-long.310/) ·
[MMLongBench-Doc (NeurIPS'24)](https://proceedings.neurips.cc/paper_files/paper/2024/file/ae0e43289bffea0c1fa34633fc608e92-Paper-Datasets_and_Benchmarks_Track.pdf) ·
[M3DocRAG (Bloomberg)](https://github.com/bloomberg/m3docrag) ·
[Meta CRAG (KDD Cup 2024)](https://www.aicrowd.com/challenges/meta-comprehensive-rag-benchmark-kdd-cup-2024) ·
[FRAMES (Google DeepMind)](https://huggingface.co/datasets/google/frames-benchmark) ·
[GraphRAG-Bench (ICLR'26)](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark) ·
[GraphRAG-Bench paper](https://arxiv.org/pdf/2506.02404) ·
[microsoft/graphrag-benchmarking-datasets](https://github.com/microsoft/graphrag-benchmarking-datasets) ·
[MultiHop-RAG (COLM'24)](https://github.com/yixuantt/MultiHop-RAG) ·
[MMESGBench](https://arxiv.org/pdf/2507.18932) ·
[MKG-RAG-Bench](https://arxiv.org/html/2606.26458v1) ·
[M-LongDoc](https://arxiv.org/html/2411.06176v1) ·
[XL-DocBench](https://arxiv.org/html/2608.00036) ·
[SynthDocBench](https://arxiv.org/html/2607.10400v1) ·
[KRETA (Korean text VQA)](https://arxiv.org/pdf/2508.19944) ·
[KOFFVQA](https://arxiv.org/pdf/2503.23730)
