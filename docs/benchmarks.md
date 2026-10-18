# 문서 GraphRAG 벤치마크

> 조사: 2026-09-22
>
> 우리 파이프라인을 남과 비교할 수 있는 공개 벤치마크를 모았다. 두 가지로 나열한다 —
> **§1 유명도 순**(대기업이 만들었거나 자사 모델 평가에 쓰는 것 → 유명 논문·학회 순),
> **§2 우리 파이프라인의 어느 단계를 재는가**. 같은 세트가 양쪽에 나온다.
> 무엇을 먼저 돌릴지는 [`roadmap.md`](roadmap.md) §0.

## 1. 유명도 순

### S급 — 프런티어 모델이면 전부 보고하는 것

빅테크 모델 카드·기술보고서에 기본으로 실리는 문서 벤치마크다(Qwen2.5-VL 기술보고서, Gemini·GPT-4o 비교표 등).

| # | 벤치마크 | 성격 | 우리 적용도 |
|---|---|---|---|
| 1 | **DocVQA** | 문서 이미지 QA의 사실상 표준 | ★★☆ 단일 페이지 |
| 2 | **ChartQA** | 차트 QA | ★★★ 차트 노드 |
| 3 | **InfographicVQA** | 복잡·비정형 레이아웃 | ★★☆ |
| 4 | **AI2D / TextVQA** | 다이어그램·장면 텍스트 | ★☆☆ |

### A급 — 유명 기업이 만든 것

| # | 벤치마크 | 만든 곳 | 내용 | 우리 적용도 |
|---|---|---|---|---|
| 5 | [CRAG](https://www.aicrowd.com/challenges/meta-comprehensive-rag-benchmark-kdd-cup-2024) | **Meta Reality Labs** (+HKUST) | KDD Cup 2024 공식 과제. 5개 도메인·8개 질문유형 RAG 종합 평가 | ★★☆ |
| 6 | [FRAMES](https://huggingface.co/datasets/google/frames-benchmark) | **Google DeepMind** | 사실성·검색·다중제약 추론을 한 번에 | ★★☆ |
| 7 | [UniDoc-Bench](https://github.com/SalesforceAIResearch/UniDoc-Bench) | **Salesforce AI Research** | 실제 PDF 70k 페이지, 8도메인, QA 1,600. text-only/image-only/융합/joint **4패러다임을 같은 프로토콜로 비교** | ★★★ 설계 선택 검증 |
| 8 | [VisDoMBench](https://aclanthology.org/2025.naacl-long.310/) | **Adobe Research** (NAACL'25) | 다문서 + 표·차트·슬라이드 | ★★★ |
| 9 | [M3DocVQA](https://github.com/bloomberg/m3docrag) | **Bloomberg** | PDF 3,000+ / 40,000+페이지 오픈도메인 | ★★☆ |
| 10 | REAL-MM-RAG | **IBM Research** | 재표현(rephrasing) 난이도 계층으로 의미 이해 검증 | ★★☆ |
| 11 | [DocLayNet](https://github.com/DS4SD/DocLayNet) / [PubTabNet](https://github.com/ibm-aur-nlp/PubTabNet) | **IBM Research** | 레이아웃 검출 / 표 구조(50만+) | ★★★ 구조층 |
| 12 | [SlideVQA](https://github.com/nttmdlab-nlp/SlideVQA) | **NTT** | 슬라이드 덱 ~20페이지 | ★★★ PPTX와 가장 가까움 |
| 13 | [SDS KoPub VDR](https://arxiv.org/pdf/2511.04910) | **삼성SDS** | 한국어 공공문서 페이지 검색 | ★★★ 한국어 |
| 14 | [graphrag-benchmarking-datasets](https://github.com/microsoft/graphrag-benchmarking-datasets) | **Microsoft** | GraphRAG 공식 벤치마킹 세트 | ★★☆ |
| 15 | [ViDoRe](https://github.com/illuin-tech/vidore-benchmark) | Illuin Technology (ColPali, ICLR'25) | 페이지 단위 시각 검색, HF 리더보드 운영 | ★★★ 검색 |
| 16 | [FinanceBench](https://github.com/patronus-ai/financebench) | Patronus AI | 금융 보고서 QA | ★★☆ |

### B급 — 유명 논문·학회 발

| # | 벤치마크 | 출처 | 내용 | 우리 적용도 |
|---|---|---|---|---|
| 17 | [MMLongBench-Doc](https://proceedings.neurips.cc/paper_files/paper/2024/file/ae0e43289bffea0c1fa34633fc608e92-Paper-Datasets_and_Benchmarks_Track.pdf) | NeurIPS'24 D&B | 문서 130개, 최대 468페이지, **교차 페이지** 전문가 주석 | ★★★ cross-page 계층 |
| 18 | [OmniDocBench](https://github.com/opendatalab/OmniDocBench) | CVPR'25 (OpenDataLab) | PDF 1,651페이지, 블록 28종 + 스팬 4종, **읽기 순서 주석**, 표는 LaTeX/HTML 이중 주석. NED·TEDS | ★★★ 구조층 표준 |
| 19 | [GraphRAG-Bench](https://github.com/GraphRAG-Bench/GraphRAG-Benchmark) | ICLR'26 (PolyU) | 사실검색 → 다단추론 → 문맥요약 → 창의생성. 구축·검색·생성 **전 구간**을 따로 평가 | ★★★ 그래프 도입 판단 |
| 20 | [HotpotQA](https://hotpotqa.github.io/) | EMNLP'18 | 다단 추론의 고전 | ★★☆ |
| 21 | [MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG) | COLM'24 (HKUST) | 질의 2,556개, 근거가 문서 2~4개에 걸침 | ★★☆ |
| 22 | 2WikiMultihopQA / MuSiQue | COLING'20 / TACL'22 | 다단 추론 표준 | ★★☆ |
| 23 | [DUDE](https://rrc.cvc.uab.es/?ch=23) | ICCV'23 경진 | 문서 ~5,000개(평균 5.7페이지), 의료·법률·기술·금융 | ★★☆ |
| 24 | MP-DocVQA | CVC Barcelona | 스캔 멀티페이지, 페이지 인덱스 추론 | ★★☆ |
| 25 | [LongDocURL](https://arxiv.org/abs/2412.18424) | 2024-12 | 평균 ~30페이지, 헤딩·링크·표 혼재 | ★★☆ |
| 26 | [MMESGBench](https://arxiv.org/pdf/2507.18932) | 2025 | ESG **기업 보고서** | ★★★ 문서 형태 유사 |
| 27 | [TAT-DQA](https://nextplusplus.github.io/TAT-DQA/) | ACM MM (NUS) | 표 + 텍스트 금융문서 추론 | ★★☆ |
| 28 | [MKG-RAG-Bench](https://arxiv.org/html/2606.26458v1) | 2026 | 멀티모달 KG-RAG 전용 | ★★☆ |
| 29 | [DocBench](https://github.com/Anni-Zou/DocBench) / [M-LongDoc](https://arxiv.org/html/2411.06176v1) / [XL-DocBench](https://arxiv.org/html/2608.00036) / [SynthDocBench](https://arxiv.org/html/2607.10400v1) | 2024~2026 | 장문·초장문 변형 | ★☆☆ |
| 30 | [SciCap](https://github.com/tingyaohsu/SciCap) | 2021~ | 그림 캡션 생성 | ★★★ 캡션 노드 평가 |

## 2. 우리 파이프라인의 어느 단계를 재는가

### A. 문서 파싱·구조 — 추출·렌더·땅따먹기·계층

| 벤치마크 | 무엇을 재나 |
|---|---|
| **OmniDocBench** | 구조 층 평가의 표준. 읽기 순서와 표 TEDS. Evidence Units 논문도 이걸로 측정했다 |
| DocLayNet | 레이아웃 영역 검출만 |
| PubTabNet / FinTabNet / SciTSR | 표 구조 인식(TEDS) — 표 노드 품질 |
| ReadingBank | 읽기 순서 — `NEXT`와 순서 판단 |
| [MPDocBench-Parse](https://arxiv.org/html/2605.22100v1), [Dr.DocBench](https://arxiv.org/html/2606.01393v1) | 멀티페이지·전문가급 파싱. 최신 비교군 |
| SciCap | 그림 캡션 — `vlm_enriched_caption` 평가 |

### B. 시각 문서 검색 — 검색 진입점

| 벤치마크 | 무엇을 재나 |
|---|---|
| **SDS KoPub VDR** | 한국어 공공문서 페이지 검색. 질문당 여러 후보 중 최적 페이지 — 한국어 코퍼스라 가장 가깝다 |
| **ViDoRe** | 페이지 단위 검색, 텍스트/그림/인포그래픽/표 × 의료·비즈니스·과학·행정 |
| MMDocIR | 장문서의 페이지 + 레이아웃 단위 검색 — 우리 노드 단위 검색과 대응 |
| REAL-MM-RAG | 재표현 난이도 계층 — 의미 이해 강건성 |

### C. 멀티페이지·멀티모달 문서 QA — 엔드투엔드

| 벤치마크 | 무엇을 재나 |
|---|---|
| **SlideVQA** | 슬라이드 덱. PPTX 경로와 가장 가깝고, MegaRAG가 주력으로 쓴 세트 |
| **MMLongBench-Doc** | 교차 페이지 질의 — 페이지 넘김(열린 조상·`holds`·그림 절단)의 직접 시험대 |
| **UniDoc-Bench** | 텍스트만 / 이미지만 / 융합을 같은 프로토콜로 비교 — 캡션과 크롭을 둘 다 가진 우리에게 곧 설계 결정 |
| **VisDoMBench** | 다문서 질의 — `REFERENCES`로 이은 문서 간 질의 |
| LongDocURL · DUDE · MP-DocVQA · M3DocVQA | 장문·스캔·오픈도메인 변형 |
| MMESGBench | 기업 보고서 형태 |
| DocVQA · InfographicVQA · ChartQA · TAT-DQA | 부분 능력 |

### D. GraphRAG 자체

| 벤치마크 | 무엇을 재나 |
|---|---|
| **GraphRAG-Bench** | 난이도 계층(사실검색 → 다단추론 → 문맥요약 → 창의생성). 구축·검색·생성 전 구간 |
| microsoft/graphrag-benchmarking-datasets | MS 공식 벤치마킹 데이터셋 모음 |
| MultiHop-RAG | 근거가 문서 2~4개에 걸친 질의 |
| HotpotQA / 2WikiMultihopQA / MuSiQue / NQ | 다단 추론 표준(대부분 위키 기반) |
| UltraDomain(LightRAG), NarrativeQA, QuALITY | 도메인·장문 |
| MKG-RAG-Bench | 멀티모달 KG-RAG 전용 |
| CMEL (MMGraphRAG) | 교차 모달 엔터티 정렬 |

## 3. 그대로 쓸 수 없는 이유 두 가지

1. **입력 형식이 안 맞는다.** 위 벤치마크는 거의 전부 PDF·이미지 기반인데 우리는 XLSX·PPTX·DOCX 원본을
   렌더한다. 그대로 쓸 수 있는 건 PDF 계열과 SlideVQA 정도이고, 나머지는 우리 강점(원본 좌표·셀 범위)을
   재주지 못한다.
2. **사내 기술 문서의 공개 벤치마크가 없다.** 사내 보고서에 해당하는 공개 세트는 없고, 형태상
   가장 가까운 것이 MMESGBench와 TAT-DQA다. 결국 **자체 gold 세트**가 필요하다 — 순차 계층 결과를
   gold로 저장해 둔 로컬 회귀 세트가 그 출발점이다.

## 4. 실제로 집을 것

유명도와 적용도를 함께 보면 셋으로 좁혀진다.

1. **SlideVQA**(NTT) — PPTX 경로를 그대로 재는 유일한 유명 세트. 슬라이드 계층·`NEXT`·크롭이 시험대에 오른다.
2. **MMLongBench-Doc**(NeurIPS'24) — 장문 PDF의 교차 페이지 질의. 페이지 넘김 처리가 정확히 이걸로 검증된다.
3. **UniDoc-Bench**(Salesforce) — 텍스트만 / 이미지만 / 융합 중 무엇이 나은지를 같은 프로토콜로 잰다.
   1차를 텍스트 전용으로 정한 판단을 재는 자리다.

한국어 코퍼스라면 **SDS KoPub VDR**(삼성SDS)을 검색 평가에 하나 더 얹는다.

단계별로는 이렇게 나눠 쓴다.

| 층 | 세트 |
|---|---|
| 구조 | OmniDocBench (읽기 순서·표 TEDS) — PDF 경로에만 직접 적용 |
| 캡션 | SciCap 방식 지표를 우리 캡션에 적용 |
| 검색 | SDS KoPub VDR(한국어) + ViDoRe |
| E2E | SlideVQA + MMLongBench-Doc + UniDoc-Bench(프로토콜만 차용) |
| 그래프 | GraphRAG-Bench의 **태스크 축**을 빌려 우리 코퍼스로 자체 Q&A 구성 |

## 5. 출처

[Qwen2.5-VL 기술보고서](https://arxiv.org/pdf/2502.13923) ·
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
[GraphRAG-Bench 논문](https://arxiv.org/pdf/2506.02404) ·
[microsoft/graphrag-benchmarking-datasets](https://github.com/microsoft/graphrag-benchmarking-datasets) ·
[MultiHop-RAG (COLM'24)](https://github.com/yixuantt/MultiHop-RAG) ·
[MMESGBench](https://arxiv.org/pdf/2507.18932) ·
[MKG-RAG-Bench](https://arxiv.org/html/2606.26458v1) ·
[M-LongDoc](https://arxiv.org/html/2411.06176v1) ·
[XL-DocBench](https://arxiv.org/html/2608.00036) ·
[SynthDocBench](https://arxiv.org/html/2607.10400v1) ·
[KRETA (한국어 텍스트 VQA)](https://arxiv.org/pdf/2508.19944) ·
[KOFFVQA](https://arxiv.org/pdf/2503.23730)
