# Multi-RAG Platform (design captured, build not started)

Originally an interview system-design question: design a platform that can host **at least
two structurally different RAG pipelines** behind one set of shared abstractions. This
directory is a placeholder that records the decisions made so far so the next session can
start straight into implementation instead of re-deciding scope.

## Decisions locked in

- **The two RAG flavors to contrast:**
  1. **Unstructured-document RAG** — classic chunk → embed → vector-search → generate over
     PDFs/text. The interesting problems: chunking strategy, embedding model choice, hybrid
     (keyword + vector) retrieval, re-ranking, citation/grounding.
  2. **Structured/SQL RAG** — text-to-SQL (or text-to-query) over a relational database, then
     generate an answer from the query result. The interesting problems: schema-aware
     prompting, query validation/safety (no arbitrary writes), handling ambiguous questions,
     grounding answers in the actual returned rows rather than hallucinated numbers.

  These were picked deliberately because they stress *different* parts of a shared platform:
  one is retrieval-heavy or "search", the other is generation-of-a-query heavy or "compute" —
  so the platform's abstractions have to be generic enough to cover both retrieval-then-read
  and generate-then-execute-then-read patterns, not just "swap the vector store."

- **Tech stack:** Go and/or Python services, deployed on AWS. Not yet decided which language
  owns which service boundary (e.g. Go for the ingestion/orchestration API, Python for the
  embedding/retrieval/LLM-calling internals is one natural split, given Python's LLM/ML
  ecosystem vs Go's strength for a concurrent, low-latency gateway) — that's one of the first
  design questions to resolve when implementation starts.

## What's still open (first things to decide when this project resumes)

1. **Shared abstraction boundary** — what does a common `Pipeline` interface look like across
   a vector-search pipeline and a text-to-SQL pipeline? Likely candidates: a common
   `retrieve(query) -> Context` + `generate(query, Context) -> Answer` contract, with each
   pipeline implementing `retrieve` completely differently (vector search vs. SQL execution).
2. **AWS service mapping** — candidate shape: API Gateway/ALB → orchestration service (ECS/
   Fargate or Lambda) → per-pipeline workers → a vector store (OpenSearch/pgvector on RDS) for
   pipeline 1 and an RDS/Aurora instance for pipeline 2, with S3 for raw document storage and
   ingestion artifacts.
3. **Multi-tenancy / routing** — how a request picks which pipeline to hit (explicit
   endpoint per pipeline vs. a router that classifies the query first).
4. **Observability** — tracing a request through retrieval + generation is the crux of
   debugging RAG systems; decide early whether to build this on OpenTelemetry from the start.
5. **Evaluation harness** — a golden-question-set eval loop per pipeline (retrieval
   precision/recall, answer faithfulness) so changes to chunking/prompting are measured, not
   vibes-checked.

## Suggested next session

Start with a `DESIGN.md` (mirroring `manga-comic-generator/DESIGN.md`'s style — decisions plus
the *why* behind each one) covering points 1–2 above, then scaffold two service skeletons
(`ingestion/`, `pipelines/doc_rag/`, `pipelines/sql_rag/`, `orchestrator/`) before writing any
AWS infra-as-code.
