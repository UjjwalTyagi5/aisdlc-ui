"""Frozen, numbered versions of a stage's artifact payload.

Three things live here, in the order they were built and depend on each other:

    phase 1  a version that cannot change once created
    phase 2  the single signature that publishes one
    phase 3  `read_upstream`, the one way a consuming agent reads another stage —
             published versions only, every read recorded

An approval is only worth taking if it points at something that cannot subsequently
change, which is why they are one module rather than three.

THE PROBLEM IT SOLVES. Agents hand work to each other through
`runs.{stage}_artifacts`, a JSONB column `patch_session_artifacts` writes IN PLACE.
Approve it and the next run replaces it: the approval record survives, the thing it
approved does not. Every later phase — a publication gate, consumers that read only
approved work, an audit answer to "what did this run build on" — rests on a version
that is frozen once created.

`runs.{stage}_artifacts` STAYS the agent's working draft. `snapshot_stage_payload`
COPIES it here. A new run creates N+1 and never edits N.

WHAT ENFORCES WHAT. The database owns the invariants, deliberately — a service-layer
rule is one careless UPDATE away from being false, and that failure would be silent:

    trigger artifact_versions_freeze   payload/hash/identity cannot be modified
    unique (project_id, stage, version) two racing runs cannot both take N
    ck_..._no_self_publication          published_by <> produced_by
    ck_..._published_by_someone         a publication names its publisher and when
    ck_..._rejection_has_reason         a rejection says why

NO COMMITS. Every function leaves the transaction open for the caller, matching
`deployment_gate` and the artifact routes — `get_db_session` sets the RLS tenant
transaction-locally and owns the single commit at request end. Committing here drops
`app.current_tenant_id` and the next statement in the same request reads an empty
table.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.orm import ArtifactConsumptionGrant, ArtifactVersion

logger = logging.getLogger(__name__)

# Terminal and non-terminal statuses. `superseded` is NOT a failure — it is a version
# that was published and has since been replaced, and it stays readable because a run
# that consumed it must keep being able to say what it built on.
STATUSES = ("draft", "published", "rejected", "superseded")

# How many times to retry a version-number collision. Two concurrent runs finishing
# the same stage is rare but real; the unique constraint makes one of them lose.
_ALLOC_RETRIES = 5


class ArtifactVersionError(RuntimeError):
    """A version could not be created or read."""


class UnknownStage(ArtifactVersionError):
    """The stage does not name an agent that can produce artifacts."""


@dataclass(frozen=True)
class VersionRef:
    """What a caller needs after a snapshot, without holding an ORM object across a
    transaction boundary."""
    id: str
    stage: str
    version: int
    status: str
    content_hash: str
    created: bool          # False when an identical payload already had a version


def canonical_hash(payload: Any) -> str:
    """sha256 of the payload in a canonical form.

    Exists so "has this actually changed?" is answerable without diffing JSONB, and so
    an audit row can name exactly what was signed.

    `sort_keys` is what makes it canonical: two payloads equal as data but built in a
    different key order MUST hash the same, or every re-run would look like a change
    and version numbers would climb without meaning. `separators` removes incidental
    whitespace for the same reason. `default=str` keeps datetimes and UUIDs from
    raising — a hash that throws on a valid payload is worse than one that stringifies.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def assert_known_stage(stage: str) -> None:
    """Validate against STAGE_ORDER rather than a hardcoded list.

    The migration deliberately has no CHECK constraint on `stage`: the valid stages
    derive from AGENT_REGISTRY, and pinning them in the schema would mean a migration
    per new agent plus a DB/code disagreement — the exact drift this workstream is
    removing. So the check lives here, against the same source the runtime uses.
    """
    from shared.services.orchestrator.progression import STAGE_ORDER  # noqa: PLC0415

    if stage not in STAGE_ORDER:
        raise UnknownStage(
            f"{stage!r} is not a stage that produces artifacts; expected one of "
            f"{list(STAGE_ORDER)}. Note these are BACKEND stage names — "
            f"'code_review', not the UI's 'review'."
        )


async def latest_version(
    db: AsyncSession, project_id: str, stage: str,
) -> Optional[ArtifactVersion]:
    """The highest-numbered version of this stage, whatever its status."""
    return (await db.execute(
        select(ArtifactVersion)
        .where(ArtifactVersion.project_id == project_id, ArtifactVersion.stage == stage)
        .order_by(ArtifactVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()


async def latest_published(
    db: AsyncSession, project_id: str, stage: str,
) -> Optional[ArtifactVersion]:
    """The newest version a human has signed off.

    THE READ EVERY CONSUMER WILL MAKE once phase 3 lands. Returning None here is a
    legitimate, meaningful answer — "no approved design exists yet" — and the caller
    must say so rather than falling back to the draft. A fallback would make the whole
    gate decorative, exactly as the tenant-wide credential fallback made "Needs a
    credential" decorative until it was removed.
    """
    return (await db.execute(
        select(ArtifactVersion)
        .where(
            ArtifactVersion.project_id == project_id,
            ArtifactVersion.stage == stage,
            ArtifactVersion.status == "published",
        )
        .order_by(ArtifactVersion.version.desc())
        .limit(1)
    )).scalar_one_or_none()


async def list_versions(
    db: AsyncSession, project_id: str, stage: Optional[str] = None,
) -> list[ArtifactVersion]:
    """Every version for a project, newest first. One stage's, or all of them."""
    stmt = select(ArtifactVersion).where(ArtifactVersion.project_id == project_id)
    if stage is not None:
        stmt = stmt.where(ArtifactVersion.stage == stage)
    return list((await db.execute(
        stmt.order_by(ArtifactVersion.stage, ArtifactVersion.version.desc())
    )).scalars().all())


async def current_working_payload(
    db: AsyncSession, project_id: str, stage: str,
) -> Any:
    """The stage's latest working payload — what a freeze captures.

    READ SERVER-SIDE, NOT SENT BY THE CLIENT. A freeze exists to capture what the AGENT
    produced; accepting the payload from the browser would let somebody freeze, publish
    and hand downstream something the agent never wrote, with the whole gate wrapped
    approvingly around it.

    Uses the same column map `persist_artifact` writes through, so the freeze reads
    exactly where the agent wrote — including `requirements_payload`, which is not
    `requirements_artifacts` and is the column the standalone Requirements agent
    actually fills.
    """
    from shared.models.orm import Run  # noqa: PLC0415
    from shared.services.artifact_service import _COLUMN_MAP  # noqa: PLC0415

    column = _COLUMN_MAP.get(stage)
    if not column:
        return None
    col = getattr(Run, column, None)
    if col is None:
        return None
    return (await db.execute(
        select(col)
        .where(Run.project_id == project_id, col.isnot(None))
        .order_by(Run.updated_at.desc())
        .limit(1)
    )).scalars().first()


async def snapshot_stage_payload(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    stage: str,
    payload: Any,
    produced_by: str,
    run_id: Optional[str] = None,
    covers: Optional[list[str]] = None,
    dedupe: bool = True,
) -> VersionRef:
    """Freeze the stage's working payload as the next version.

    Returns the EXISTING version when `dedupe` and the latest one already holds an
    identical payload. A stage re-running without producing anything new should not
    manufacture a version to approve — version numbers that climb without meaning are
    how a reviewer learns to stop reading them.

    `produced_by` is required and must be a real person: it is half of the
    self-publication check the schema enforces. An ownerless snapshot would produce a
    row that ANY publisher could sign, which is the rule inverted.
    """
    assert_known_stage(stage)
    if not produced_by:
        raise ArtifactVersionError(
            "produced_by is required — a version nobody produced cannot be checked "
            "against its publisher, so self-publication could not be refused"
        )
    if payload is None:
        raise ArtifactVersionError(
            f"refusing to snapshot a null payload for stage {stage!r}; there is "
            "nothing to freeze and an empty version would look like approved work"
        )

    content_hash = canonical_hash(payload)

    if dedupe:
        current = await latest_version(db, project_id, stage)
        # THE COVERED DOCUMENTS ARE PART OF WHAT IS BEING FROZEN, so an identical
        # payload with a DIFFERENT document set is a different unit and must become a
        # new version. Comparing the payload alone silently discarded `covers`: the
        # caller ticked documents, got back the existing version that covered none of
        # them, and was told "frozen" — the signed unit was not the one chosen, and
        # nothing said so.
        same_covers = (
            current is not None
            and sorted(str(x) for x in (current.covers or []))
            == sorted(str(x) for x in (covers or []))
        )
        if current is not None and current.content_hash == content_hash and same_covers:
            logger.info(
                "artifact version: %s/%s v%s already holds this payload (%s), reusing",
                project_id, stage, current.version, content_hash[:12],
            )
            return VersionRef(
                id=str(current.id), stage=stage, version=current.version,
                status=current.status, content_hash=content_hash, created=False,
            )

    # Allocate N+1 under the unique constraint. Read-then-insert is racy by nature, so
    # the constraint is the actual guard and this loop just retries the loser rather
    # than failing a run over a collision.
    for attempt in range(_ALLOC_RETRIES):
        next_version = ((await db.execute(
            text(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM artifact_versions "
                "WHERE project_id = CAST(:p AS uuid) AND stage = :s"
            ),
            {"p": project_id, "s": stage},
        )).scalar()) or 1

        row = ArtifactVersion(
            tenant_id=tenant_id, project_id=project_id, stage=stage,
            run_id=run_id, version=next_version, payload=payload,
            content_hash=content_hash, covers=list(covers or []),
            status="draft", produced_by=produced_by,
        )
        db.add(row)
        try:
            await db.flush()
        except IntegrityError:
            # Another run took this number. SAVEPOINT-less rollback would discard the
            # caller's whole transaction, so the caller must be inside one that can
            # absorb this — every route here runs in `get_db_session`'s transaction.
            await db.rollback()
            if attempt == _ALLOC_RETRIES - 1:
                raise ArtifactVersionError(
                    f"could not allocate a version number for {project_id}/{stage} "
                    f"after {_ALLOC_RETRIES} attempts"
                )
            logger.warning(
                "artifact version: %s/%s v%s taken, retrying (%s/%s)",
                project_id, stage, next_version, attempt + 1, _ALLOC_RETRIES,
            )
            continue

        logger.info(
            "artifact version: froze %s/%s v%s (%s) produced_by=%s",
            project_id, stage, next_version, content_hash[:12], produced_by,
        )
        return VersionRef(
            id=str(row.id), stage=stage, version=next_version, status="draft",
            content_hash=content_hash, created=True,
        )

    raise ArtifactVersionError("unreachable")  # pragma: no cover


# ── the publication gate (phase 2) ───────────────────────────────────────────


class PublicationRefused(ArtifactVersionError):
    """The publication or rejection was not allowed. Carries `.code` so a route can
    map it to the right HTTP status rather than flattening everything to 400."""

    def __init__(self, message: str, code: str = "refused") -> None:
        super().__init__(message)
        self.code = code


async def get_version(
    db: AsyncSession, project_id: str, stage: str, version: int,
) -> Optional[ArtifactVersion]:
    return (await db.execute(
        select(ArtifactVersion).where(
            ArtifactVersion.project_id == project_id,
            ArtifactVersion.stage == stage,
            ArtifactVersion.version == version,
        )
    )).scalar_one_or_none()


async def publish_version(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    stage: str,
    version: int,
    published_by: str,
    ) -> ArtifactVersion:
    """Sign a version off. One signature, and every consumer may then read it.

    FOUR REFUSALS, each a way an approval could mean less than it appears to:

      self-publication   The producer cannot sign their own work. The agent runs AS A
                         PERSON, so the comparison is always available. Same rule as
                         the run gate and the deployment gate. The schema enforces it
                         too — this check exists to give a usable message rather than
                         a constraint name.
      a decided version  Publishing a rejected version would quietly overturn a
                         decision somebody took. Produce a new version instead.
      going backwards    Publishing a version older than the current published one
                         would supersede newer approved work with older content.
                         Legitimate occasionally — that is a phase 4 request, not a
                         silent default.
      an unknown stage   Fails closed, consistent with everything else here.

    The previous published version becomes `superseded`, never deleted: a run that
    consumed it must keep being able to say what it built on.

    NO COMMIT — the caller's request-scoped session owns it.
    """
    assert_known_stage(stage)
    if not published_by:
        raise PublicationRefused(
            "a publication must name its publisher", code="no_publisher")

    row = await get_version(db, project_id, stage, version)
    if row is None:
        raise PublicationRefused(
            f"{stage} v{version} does not exist in this project", code="not_found")

    if row.status == "published":
        # Idempotent: a double-click must not raise, and must not re-supersede.
        return row
    if row.status in ("rejected", "superseded"):
        raise PublicationRefused(
            f"{stage} v{version} is {row.status} and cannot be published; "
            "produce a new version instead",
            code="already_decided",
        )

    if row.produced_by == published_by:
        raise PublicationRefused(
            "the person whose run produced this version cannot also publish it",
            code="self_publication",
        )

    current = await latest_published(db, project_id, stage)
    if current is not None and current.version > version:
        raise PublicationRefused(
            f"{stage} v{current.version} is already published; publishing the older "
            f"v{version} would supersede newer approved work",
            code="would_go_backwards",
        )

    now = _utcnow()
    if current is not None:
        current.status = "superseded"
        # PHASE 5. Consumers that built on the version being replaced are TOLD, not
        # switched. Silent upgrade is how a design change reaches production without
        # anyone reviewing it, and it is indistinguishable from correct behaviour until
        # something breaks.
        await _notify_supersession(
            db, tenant_id=tenant_id, project_id=project_id,
            superseded=current, replacement=row,
        )

    row.status = "published"
    row.published_by = published_by
    row.published_at = now

    _audit(
        db, tenant_id=tenant_id, actor_id=published_by,
        event_type="artifact_version_publish", row=row,
        extra={
            "supersedes": current.version if current is not None else None,
            # THE PAYLOAD ITSELF, so the evidence of what was signed does not depend
            # on the artifact_versions row surviving unedited. The freeze trigger makes
            # that unlikely; an audit trail that trusts the thing it audits is still
            # the wrong shape.
            "payload": row.payload,
        },
    )
    logger.info(
        "artifact version: %s/%s v%s published by %s (%s), supersedes %s",
        project_id, stage, version, published_by, row.content_hash[:12],
        current.version if current is not None else "-",
    )
    return row


async def _notify_supersession(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    superseded: ArtifactVersion,
    replacement: ArtifactVersion,
) -> None:
    """Tell whoever built on the old version that a new one exists.

    ADDRESSED TO THE CONSUMING STAGE'S OWNER, scoped to the project — the role that
    signs that stage off is the one who has to decide whether the change matters. The
    person who happened to run the agent may not even still be on the project.

    ONE NOTIFICATION PER CONSUMING STAGE, not per consumption row. An agent that read
    its upstream four times in a turn produced four rows and has not become four
    audiences; four identical bells is how a bell stops being read.

    BEST-EFFORT, like every other `emit` caller: a notification that cannot be written
    must never roll back the publication it was announcing. The state it announces is
    still queryable either way.
    """
    from shared.governance.routing import agent_owner_role_or_none  # noqa: PLC0415
    from shared.models.orm import ArtifactConsumption  # noqa: PLC0415
    from shared.services import notifications  # noqa: PLC0415

    consumers = list((await db.execute(
        select(ArtifactConsumption.consumer_stage)
        .where(ArtifactConsumption.version_id == superseded.id)
        .distinct()
    )).scalars().all())
    if not consumers:
        return

    for consumer_stage in consumers:
        owner = agent_owner_role_or_none(consumer_stage)
        if owner is None:
            # A consumer whose owner cannot be resolved is a data problem, not a
            # reason to skip silently — the whole of phase 0 exists because a missing
            # owner used to answer "project_admin" and nobody noticed.
            logger.warning(
                "supersession notice skipped: consumer stage %r has no owner role",
                consumer_stage,
            )
            continue
        try:
            await notifications.emit(
                db,
                tenant_id=tenant_id,
                kind="artifact_superseded",
                title=(
                    f"{superseded.stage} v{replacement.version} published — "
                    f"{consumer_stage} last built on v{superseded.version}"
                ),
                body=(
                    f"{consumer_stage} consumed {superseded.stage} "
                    f"v{superseded.version}, which has been superseded by "
                    f"v{replacement.version} (published by {replacement.published_by}). "
                    f"Nothing has changed automatically — the next {consumer_stage} run "
                    f"will pick up v{replacement.version}."
                ),
                href=f"/projects/{project_id}/{consumer_stage}",
                recipient_role=owner,
                recipient_scope_kind="project",
                recipient_scope_id=str(project_id),
                project_id=str(project_id),
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "supersession notice failed for %s (project %s)",
                consumer_stage, project_id, exc_info=True,
            )


async def reject_version(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    stage: str,
    version: int,
    rejected_by: str,
    reason: str,
) -> ArtifactVersion:
    """Refuse a version, with a reason.

    A rejection is a decision, not a deletion: the row stays readable so the next run
    can see what was turned down and why. An unexplained rejection is indistinguishable
    from a mistake to whoever has to act on it, so `reason` is required here and by the
    schema.
    """
    assert_known_stage(stage)
    if not (reason or "").strip():
        raise PublicationRefused("a rejection must say why", code="no_reason")

    row = await get_version(db, project_id, stage, version)
    if row is None:
        raise PublicationRefused(
            f"{stage} v{version} does not exist in this project", code="not_found")
    if row.status != "draft":
        raise PublicationRefused(
            f"{stage} v{version} is {row.status} and cannot be rejected",
            code="already_decided",
        )
    if row.produced_by == rejected_by:
        # Symmetric with publication. Self-rejection is less dangerous but it is still
        # a decision recorded as independent review when it was not.
        raise PublicationRefused(
            "the person whose run produced this version cannot also decide it",
            code="self_publication",
        )

    row.status = "rejected"
    row.rejection_reason = reason.strip()

    _audit(
        db, tenant_id=tenant_id, actor_id=rejected_by,
        event_type="artifact_version_reject", row=row,
        extra={"reason": row.rejection_reason},
    )
    logger.info(
        "artifact version: %s/%s v%s rejected by %s", project_id, stage, version,
        rejected_by,
    )
    return row


def _utcnow():
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc)


def _audit(db: AsyncSession, *, tenant_id: str, actor_id: str, event_type: str,
           row: ArtifactVersion, extra: dict) -> None:
    """One audit row per decision, carrying the content hash.

    `content_hash` is what makes the entry evidence rather than a note: it names
    exactly which bytes were signed, and stays comparable after the fact.
    """
    from shared.models.orm import AuditEvent  # noqa: PLC0415

    db.add(AuditEvent(
        tenant_id=tenant_id,
        actor_id=actor_id,
        event_type=event_type,
        resource_type="artifact_version",
        resource_id=str(row.id),
        payload={
            "project_id": str(row.project_id),
            "stage": row.stage,
            "version": row.version,
            "content_hash": row.content_hash,
            "produced_by": row.produced_by,
            "covers": row.covers,
            **extra,
        },
    ))


# ── consumption (phase 3) ────────────────────────────────────────────────────


@dataclass(frozen=True)
class UpstreamRead:
    """What a consuming agent gets back when it asks for an upstream stage.

    `payload is None` is a LEGITIMATE, MEANINGFUL ANSWER — "nothing approved yet" —
    and `reason` says which of the several nothings it is. The calling tool must
    surface that rather than falling back to the draft.
    """
    stage: str
    payload: Any = None
    version: Optional[int] = None
    content_hash: Optional[str] = None
    published_by: Optional[str] = None
    reason: Optional[str] = None
    #: True when publication was NOT enforced for this project, so `payload` is the
    #: working draft rather than an approved version. The caller needs this to avoid
    #: telling a user something was approved when nothing was.
    unenforced: bool = False
    #: True when this version reached the consumer through an explicit
    #: `artifact_consumption` grant rather than by being published. The consumer is
    #: reading something the owner allowed AS AN EXCEPTION, and anything reporting on
    #: the run has to be able to say so — an exception recorded as routine is how the
    #: next reviewer learns the gate means nothing.
    via_grant: bool = False
    #: Documents this consumer may read: the ones the published version COVERS, plus
    #: every approved project-level document. Metadata only — `read_document` fetches
    #: the text, because inlining a 200-page PDF into every upstream read would cost
    #: context on every turn whether or not the agent wanted it.
    documents: list = field(default_factory=list)

    @property
    def found(self) -> bool:
        return self.payload is not None


async def enforcement_enabled(db: AsyncSession, project_id: str) -> bool:
    """Whether this project reads only published versions.

    FAILS SAFE TOWARDS TODAY'S BEHAVIOUR, not towards enforcement: a project row that
    cannot be read returns False. Enforcement turning itself on because a lookup
    failed would stop every agent in the project with a message that looks like an
    outage, which is a worse failure than continuing to do what it did yesterday.
    """
    value = (await db.execute(
        text(
            "SELECT enforce_artifact_publication FROM projects "
            "WHERE id = CAST(:p AS uuid)"
        ),
        {"p": project_id},
    )).scalar()
    return bool(value)


async def granted_version(
    db: AsyncSession, project_id: str, stage: str, consumer_stage: str,
) -> Optional[ArtifactVersion]:
    """A version this consumer has been explicitly granted, if any.

    NARROW BY CONSTRUCTION — the grant names one version and one consuming stage, so
    this can only ever return something a human allowed for this exact pair. There is
    deliberately no "may read drafts" grant to look up.

    Revoked grants are excluded; the row stays so a run that read under it remains
    explicable, but it stops letting anything through.

    Ordered newest-granted-first so that if an owner has allowed more than one version
    over time, the most recent decision is the operative one.
    """
    return (await db.execute(
        select(ArtifactVersion)
        .join(
            ArtifactConsumptionGrant,
            ArtifactConsumptionGrant.version_id == ArtifactVersion.id,
        )
        .where(
            ArtifactVersion.project_id == project_id,
            ArtifactVersion.stage == stage,
            ArtifactConsumptionGrant.consumer_stage == consumer_stage,
            ArtifactConsumptionGrant.revoked_at.is_(None),
        )
        .order_by(ArtifactConsumptionGrant.granted_at.desc())
        .limit(1)
    )).scalar_one_or_none()


# ── documents a consumer may read (phase 6) ──────────────────────────────────
#
#   project-level, approved                every agent
#   agent-level, COVERED by a published    every agent
#     version
#   agent-level, approved but not covered  its own agent only
#   pending or rejected                    nobody
#
# Approved means "fit to exist in the project's record". Covered by a published version
# means "part of the signed-off unit this stage handed downstream". Two different
# questions, and the gate exists for the second: a document can be a perfectly good
# document and still not be something another agent should build on.


def _document_row(a: "Artifact", source: str) -> dict[str, Any]:  # noqa: F821
    """Metadata only, and deliberately no blob path or URL.

    A path tells an agent where the bytes live, which is not something it should be
    reasoning about — `read_document` resolves the id and re-checks permission. Handing
    over a location would be a second, unguarded way in.
    """
    return {
        "id": str(a.id),
        "title": (a.blob_path or "").rsplit("/", 1)[-1] or a.artifact_type,
        "type": a.artifact_type,
        "scope": "project" if a.stage is None else "agent",
        "stage": a.stage,
        "sizeBytes": a.size_bytes,
        "approvedBy": a.approved_by,
        "approvedAt": a.approved_at.isoformat() if a.approved_at else None,
        #: "covered" (this stage signed it off) or "project" (project-wide). Says WHY
        #: the agent is allowed to see it, which an auditor needs and the agent's own
        #: reasoning sometimes does too.
        "via": source,
    }


async def readable_documents(
    db: AsyncSession,
    project_id: str,
    *,
    covered_ids: Optional[list] = None,
) -> list[dict[str, Any]]:
    """The documents a consuming agent may read, per the rule above."""
    from shared.models.orm import Artifact  # noqa: PLC0415

    out: list[dict[str, Any]] = []

    # Project-level: approved is enough. A project-wide policy IS context for every
    # agent by definition; requiring each to request it separately would be ceremony
    # with a predictable answer.
    project_wide = (await db.execute(
        select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.stage.is_(None),
            Artifact.approval_status == "approved",
        )
    )).scalars().all()
    out.extend(_document_row(a, "project") for a in project_wide)

    # Agent-level: only what the published version named. An approved-but-uncovered
    # document belongs to its own stage and stops there.
    if covered_ids:
        wanted = [str(x) for x in covered_ids if x]
        if wanted:
            covered = (await db.execute(
                select(Artifact).where(
                    Artifact.project_id == project_id,
                    Artifact.id.in_(wanted),
                    # Covered by a version is not enough on its own: a document that
                    # was later REJECTED must stop being readable even though the
                    # frozen version still names it.
                    Artifact.approval_status == "approved",
                )
            )).scalars().all()
            out.extend(_document_row(a, "covered") for a in covered)
    return out


async def read_upstream(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    stage: str,
    consumer_stage: str,
    consumer_run_id: Optional[str] = None,
    consumed_by: Optional[str] = None,
    record: bool = True,
) -> UpstreamRead:
    """The one way a consuming agent reads another stage's approved output.

    Replaces four hand-rolled readers that each took the latest non-null payload
    ordered by `created_at desc` — none of which ever asked whether a human had
    accepted it.

    THERE IS NO FALLBACK TO THE DRAFT. When nothing is published this returns
    `payload=None` with a reason, and the calling tool must say so. A fallback would
    make the gate decorative in exactly the way the tenant-wide credential fallback
    made "Needs a credential" decorative until it was removed — the feature would
    appear to work while never once refusing anything.

    Every successful read is recorded in `artifact_consumptions`, which is what makes
    "what did this run build on" answerable later.

    NO COMMIT — the caller's session owns it.
    """
    try:
        assert_known_stage(stage)
    except UnknownStage as exc:
        return UpstreamRead(stage=stage, reason=str(exc))

    row = await latest_published(db, project_id, stage)
    via_grant = False
    if row is None:
        # An OWNER-GRANTED EXCEPTION, if one exists. This is the only way an
        # unpublished version reaches a consumer, and it exists because somebody with
        # the standing to judge it said yes to this exact version for this exact
        # consumer — not because the gate quietly gave up.
        row = await granted_version(db, project_id, stage, consumer_stage)
        via_grant = row is not None
    if row is None:
        return UpstreamRead(
            stage=stage,
            reason=(
                f"No approved {stage} exists for this project. A {stage} version has "
                f"to be published before another agent may build on it."
            ),
        )

    if record:
        await record_consumption(
            db, tenant_id=tenant_id, project_id=project_id, version=row,
            consumer_stage=consumer_stage, consumer_run_id=consumer_run_id,
            consumed_by=consumed_by, via_grant=via_grant,
        )

    return UpstreamRead(
        stage=stage, payload=row.payload, version=row.version,
        content_hash=row.content_hash, published_by=row.published_by,
        via_grant=via_grant,
        # The documents this signed unit named, plus the project-wide ones. Metadata
        # only; `read_document` fetches text.
        documents=await readable_documents(
            db, project_id, covered_ids=list(row.covers or []),
        ),
    )


async def record_consumption(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    version: ArtifactVersion,
    consumer_stage: str,
    consumer_run_id: Optional[str] = None,
    consumed_by: Optional[str] = None,
    via_grant: bool = False,
) -> None:
    """One row per read. Deliberately not deduplicated — see the migration."""
    from shared.models.orm import ArtifactConsumption  # noqa: PLC0415

    db.add(ArtifactConsumption(
        tenant_id=tenant_id,
        project_id=project_id,
        version_id=version.id,
        producing_stage=version.stage,
        version=version.version,
        consumer_stage=consumer_stage,
        consumer_run_id=consumer_run_id,
        consumed_by=consumed_by,
        via_grant=via_grant,
    ))
    logger.info(
        "artifact consumption: %s read %s v%s in project %s%s",
        consumer_stage, version.stage, version.version, project_id,
        " (BY GRANT, not published)" if via_grant else "",
    )


async def record_document_consumption(
    db: AsyncSession,
    *,
    tenant_id: str,
    project_id: str,
    artifact_id: str,
    producing_stage: Optional[str],
    consumer_stage: str,
    consumer_run_id: Optional[str] = None,
    consumed_by: Optional[str] = None,
    via_grant: bool = False,
) -> None:
    """Record that a run read a DOCUMENT.

    Same table as a version read, because "what did this run build on" is one question
    and answering it from two tables means every caller unions them and one eventually
    forgets. `version_id` is NULL here and `artifact_id` is set; a CHECK requires one of
    the two, so a row that points at nothing cannot exist.
    """
    from shared.models.orm import ArtifactConsumption  # noqa: PLC0415

    db.add(ArtifactConsumption(
        tenant_id=tenant_id,
        project_id=project_id,
        artifact_id=artifact_id,
        version_id=None,
        # None for a project-level document, which belongs to no stage.
        producing_stage=producing_stage,
        version=None,
        consumer_stage=consumer_stage,
        consumer_run_id=consumer_run_id,
        consumed_by=consumed_by,
        via_grant=via_grant,
    ))
    logger.info(
        "artifact consumption: %s read document %s in project %s",
        consumer_stage, artifact_id, project_id,
    )


async def consumptions_for_run(
    db: AsyncSession, run_id: str,
) -> list["ArtifactConsumption"]:  # noqa: F821
    """What one run built on. The auditor's question, answerable from one index."""
    from shared.models.orm import ArtifactConsumption  # noqa: PLC0415

    return list((await db.execute(
        select(ArtifactConsumption)
        .where(ArtifactConsumption.consumer_run_id == run_id)
        .order_by(ArtifactConsumption.consumed_at)
    )).scalars().all())


# ── the evidence view (phase 7) ──────────────────────────────────────────────


async def run_evidence(db: AsyncSession, run_id: str) -> list[dict[str, Any]]:
    """WHAT DID THIS RUN BUILD ON — with the content hash of each thing it read.

    The hash is what makes this evidence rather than a note. "Deployment read design
    v2" is a claim about a row that could since have been renumbered or reissued;
    "deployment read the payload hashing to 5041bf1f" is checkable against the audit
    entry written when that version was signed.

    One row per READ, not per version: an agent that read its upstream twice in a turn
    did so, and collapsing that would edit history to look tidier than it was.
    """
    from shared.models.orm import ArtifactConsumption  # noqa: PLC0415

    rows = (await db.execute(
        select(ArtifactConsumption, ArtifactVersion)
        .join(ArtifactVersion, ArtifactVersion.id == ArtifactConsumption.version_id)
        .where(ArtifactConsumption.consumer_run_id == run_id)
        .order_by(ArtifactConsumption.consumed_at)
    )).all()

    return [
        {
            "producingStage": c.producing_stage,
            "version": c.version,
            "consumerStage": c.consumer_stage,
            "consumedBy": c.consumed_by,
            "consumedAt": c.consumed_at.isoformat() if c.consumed_at else None,
            # HOW it was allowed, not just that it was. An exception listed like
            # routine approved work is the failure this whole feature circles.
            "viaGrant": bool(c.via_grant),
            "contentHash": v.content_hash,
            "producedBy": v.produced_by,
            "publishedBy": v.published_by,
            # The version's status NOW, which may have moved since it was read — a
            # run that consumed something later superseded is exactly the case
            # somebody is looking for.
            "statusNow": v.status,
        }
        for c, v in rows
    ]


async def version_consumers(
    db: AsyncSession, project_id: str, stage: str, version: int,
) -> list[dict[str, Any]]:
    """WHAT CONSUMED THIS VERSION — the blast radius of one signed artifact.

    The question asked after a design turns out to be wrong: everything downstream
    that was built on it, and therefore everything that has to be looked at again.
    """
    from shared.models.orm import ArtifactConsumption  # noqa: PLC0415

    rows = (await db.execute(
        select(ArtifactConsumption)
        .join(ArtifactVersion, ArtifactVersion.id == ArtifactConsumption.version_id)
        .where(
            ArtifactVersion.project_id == project_id,
            ArtifactVersion.stage == stage,
            ArtifactVersion.version == version,
        )
        .order_by(ArtifactConsumption.consumed_at)
    )).scalars().all()

    return [
        {
            "consumerStage": c.consumer_stage,
            "consumerRunId": str(c.consumer_run_id) if c.consumer_run_id else None,
            "consumedBy": c.consumed_by,
            "consumedAt": c.consumed_at.isoformat() if c.consumed_at else None,
            "viaGrant": bool(c.via_grant),
        }
        for c in rows
    ]


# ── visibility (phase 5) ─────────────────────────────────────────────────────


async def consumption_matrix(
    db: AsyncSession, project_id: str,
) -> list[dict[str, Any]]:
    """Who may read whose artifacts on this project, one row per producing stage.

    THE HALF OF THE ASK THAT IS NOT ENFORCEMENT. Somebody has to be able to SEE which
    agent may build on which, and who to ask when it may not — otherwise the first
    time anyone learns the rules is when an agent refuses.

    Per producing stage: the owning role (who signs it off), what is currently
    published, and the state of every consuming stage against it:

        open           a published version exists — any consumer may read it
        granted        nothing published, but THIS consumer has an owner-issued grant
        needs_request  nothing published and no grant; ask the owner
        self           a stage does not consume itself

    Derived from STAGE_ORDER and the live owner map rather than a second hardcoded
    table — a matrix that could disagree with what the gate actually does would be
    worse than no matrix.
    """
    from shared.governance.routing import agent_owner_role_or_none  # noqa: PLC0415
    from shared.services.orchestrator.progression import STAGE_ORDER  # noqa: PLC0415

    published = {
        v.stage: v for v in (await db.execute(
            select(ArtifactVersion).where(
                ArtifactVersion.project_id == project_id,
                ArtifactVersion.status == "published",
            )
        )).scalars().all()
    }
    grants = {
        (stage, consumer)
        for stage, consumer in (await db.execute(
            select(ArtifactVersion.stage, ArtifactConsumptionGrant.consumer_stage)
            .join(
                ArtifactConsumptionGrant,
                ArtifactConsumptionGrant.version_id == ArtifactVersion.id,
            )
            .where(
                ArtifactVersion.project_id == project_id,
                ArtifactConsumptionGrant.revoked_at.is_(None),
            )
        )).all()
    }

    rows: list[dict[str, Any]] = []
    for producer in STAGE_ORDER:
        live = published.get(producer)
        consumers = {}
        for consumer in STAGE_ORDER:
            if consumer == producer:
                consumers[consumer] = "self"
            elif live is not None:
                consumers[consumer] = "open"
            elif (producer, consumer) in grants:
                consumers[consumer] = "granted"
            else:
                consumers[consumer] = "needs_request"
        rows.append({
            "stage": producer,
            "ownerRole": agent_owner_role_or_none(producer),
            "publishedVersion": live.version if live is not None else None,
            "publishedBy": live.published_by if live is not None else None,
            "consumers": consumers,
        })
    return rows
