from __future__ import annotations

from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from graphme.domain.models import Answer, EvaluationRecord


class QueryService(Protocol):
    def query(self, *, document_id: str, question: str) -> Answer: ...


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_evidence_ids: tuple[str, ...]


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: Literal["test", "live"]
    cases: tuple[EvaluationCase, ...]


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profile: Literal["test", "live"]
    records: tuple[EvaluationRecord, ...]
    evidence_recall: float
    failed_case_ids: tuple[str, ...]

    def render_text(self) -> str:
        return (
            f"profile={self.profile}\n"
            f"cases={len(self.records)}\n"
            f"evidence_recall={self.evidence_recall:.3f}\n"
            f"failed_cases={','.join(self.failed_case_ids)}"
        )


class EvaluationService:
    def __init__(
        self,
        *,
        query_service: QueryService,
        expected_profile: Literal["test", "live"] | None = None,
    ) -> None:
        self._query_service = query_service
        self._expected_profile = expected_profile

    @staticmethod
    def read_suite(suite: Path) -> EvaluationSuite:
        return EvaluationSuite.model_validate_json(suite.read_text(encoding="utf-8"))

    def evaluate_record(self, suite: Path) -> EvaluationReport:
        definition = self.read_suite(suite)
        if (
            self._expected_profile is not None
            and definition.profile != self._expected_profile
        ):
            raise ValueError(
                f"evaluation suite profile {definition.profile!r} does not match "
                f"runtime profile {self._expected_profile!r}"
            )
        records = tuple(
            self._evaluate_isolated(case, definition.profile)
            for case in definition.cases
        )
        retrieved = sum(record.evidence_retrieved for record in records)
        failed_case_ids = tuple(
            record.case_id for record in records if not record.evidence_retrieved
        )
        return EvaluationReport(
            profile=definition.profile,
            records=records,
            evidence_recall=retrieved / len(records) if records else 0.0,
            failed_case_ids=failed_case_ids,
        )

    def evaluate(self, suite: Path) -> str:
        return self.evaluate_record(suite).render_text()

    def _evaluate_isolated(
        self, case: EvaluationCase, profile: Literal["test", "live"]
    ) -> EvaluationRecord:
        try:
            return self._evaluate(case, profile)
        except Exception as error:
            return EvaluationRecord(
                case_id=case.case_id,
                question=case.question,
                expected_evidence_ids=case.expected_evidence_ids,
                retrieved_evidence_ids=(),
                evidence_retrieved=False,
                profile=profile,
                failure_reason=f"{type(error).__name__}: {error}",
            )

    def _evaluate(
        self, case: EvaluationCase, profile: Literal["test", "live"]
    ) -> EvaluationRecord:
        answer = self._query_service.query(
            document_id=case.document_id,
            question=case.question,
        )
        retrieved_evidence_ids = tuple(
            citation.evidence_id for citation in answer.citations
        )
        return EvaluationRecord(
            case_id=case.case_id,
            question=case.question,
            expected_evidence_ids=case.expected_evidence_ids,
            retrieved_evidence_ids=retrieved_evidence_ids,
            evidence_retrieved=set(case.expected_evidence_ids).issubset(
                retrieved_evidence_ids
            ),
            profile=profile,
        )
