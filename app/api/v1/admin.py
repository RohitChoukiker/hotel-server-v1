"""Data-operator and administrator HTTP endpoints."""

import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile

from app.api.responses import listing, success
from app.dependencies import (
    CacheDep,
    SessionDep,
    StorageDep,
    TaskDispatcherDep,
    rate_limit,
    require_roles,
)
from app.dto.admin import (
    AlgorithmVersionRead,
    DashboardRead,
    DataQualityIssueRead,
    ImportJobRead,
    JobAccepted,
    ScrapeFailureRead,
    ScrapeRunCreate,
    ScrapeRunRead,
    SystemSettingRead,
    SystemSettingWrite,
)
from app.dto.common import ListResponse, SuccessResponse
from app.dto.locations import CityRead, CityWrite
from app.dto.users import UserProfile, UserRoleUpdate, UserStatusUpdate
from app.enums import ImportType, ScopeType, UserRole
from app.models import User
from app.services.admin import AdminService
from app.services.imports import ImportService
from app.services.scraper import ScrapeService
from app.services.users import UserService

router = APIRouter(prefix="/admin", tags=["admin"])
operator = require_roles(UserRole.DATA_OPERATOR, UserRole.ADMIN)
admin_only = require_roles(UserRole.ADMIN)


async def _upload_chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await file.read(1024 * 1024):
        yield chunk


async def _create_import(
    kind: ImportType,
    file: UploadFile,
    source_code: str,
    country_id: uuid.UUID | None,
    region_id: uuid.UUID | None,
    session: SessionDep,
    storage: StorageDep,
    dispatcher: TaskDispatcherDep,
    actor_id: uuid.UUID,
) -> JobAccepted:
    job = await ImportService(session, storage).create_job(
        kind,
        source_code,
        file.filename or "import.csv",
        _upload_chunks(file),
        country_id,
        region_id,
        actor_id,
    )
    dispatcher.import_csv(job.id)
    return JobAccepted(job_id=job.id, status=job.status)


@router.post(
    "/imports/hotels",
    response_model=SuccessResponse[JobAccepted],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def import_hotels(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
    file: UploadFile = File(...),
    source_code: str = "TRIPADVISOR",
    country_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
) -> SuccessResponse[JobAccepted]:
    """Stage and queue an idempotent hotel CSV import."""
    data = await _create_import(
        ImportType.HOTELS,
        file,
        source_code,
        country_id,
        region_id,
        session,
        storage,
        dispatcher,
        actor.id,
    )
    return success(request, data)


@router.post(
    "/imports/reviews",
    response_model=SuccessResponse[JobAccepted],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def import_reviews(
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
    file: UploadFile = File(...),
    source_code: str = "TRIPADVISOR",
) -> SuccessResponse[JobAccepted]:
    """Stage and queue an idempotent review CSV import."""
    data = await _create_import(
        ImportType.REVIEWS,
        file,
        source_code,
        None,
        None,
        session,
        storage,
        dispatcher,
        actor.id,
    )
    return success(request, data)


@router.get("/imports", response_model=ListResponse[ImportJobRead])
async def imports(
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> ListResponse[ImportJobRead]:
    """List import jobs."""
    data, pagination = await AdminService(session).imports(limit, cursor)
    return listing(request, data, pagination)


@router.get("/imports/{job_id}", response_model=SuccessResponse[ImportJobRead])
async def import_job(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
) -> SuccessResponse[ImportJobRead]:
    """Return one import job."""
    return success(request, await AdminService(session).import_job(job_id))


@router.get("/scrape-runs", response_model=SuccessResponse[list[ScrapeRunRead]])
async def scrape_runs(
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
) -> SuccessResponse[list[ScrapeRunRead]]:
    """List scraper runs."""
    return success(request, await ScrapeService(session).list_runs())


@router.post(
    "/scrape-runs",
    response_model=SuccessResponse[JobAccepted],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def start_scrape_run(
    payload: ScrapeRunCreate,
    request: Request,
    session: SessionDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
) -> SuccessResponse[JobAccepted]:
    """Queue a bounded scrape using an operator-supplied source geo ID."""
    run = await ScrapeService(session).start(actor.id, payload)
    dispatcher.scrape_run(run.id)
    return success(request, JobAccepted(job_id=run.id, status=run.status))


@router.get("/scrape-runs/{run_id}", response_model=SuccessResponse[ScrapeRunRead])
async def scrape_run(
    run_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
) -> SuccessResponse[ScrapeRunRead]:
    """Return one scraper run."""
    return success(request, await ScrapeService(session).run(run_id))


@router.get(
    "/scrape-runs/{run_id}/failures",
    response_model=SuccessResponse[list[ScrapeFailureRead]],
)
async def scrape_failures(
    run_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
) -> SuccessResponse[list[ScrapeFailureRead]]:
    """Return scraper failures."""
    return success(request, await ScrapeService(session).failures(run_id))


@router.post(
    "/scrape-runs/{run_id}/retry-failures",
    response_model=SuccessResponse[dict[str, int]],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def retry_scrape_failures(
    run_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
) -> SuccessResponse[dict[str, int]]:
    """Queue unresolved scraper failures for bounded retry."""
    failure_count = await ScrapeService(session).retry_failures(actor.id, run_id)
    if failure_count:
        dispatcher.scrape_run(run_id)
    return success(request, {"queued": failure_count})


@router.post(
    "/scoring/process-reviews",
    response_model=SuccessResponse[dict[str, bool]],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def process_reviews(
    request: Request,
    session: SessionDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
) -> SuccessResponse[dict[str, bool]]:
    """Queue review attribute extraction."""
    await AdminService(session).audit_action(
        actor.id, "review_processing_started", "scoring"
    )
    dispatcher.process_reviews()
    return success(request, {"queued": True})


@router.post(
    "/scoring/recalculate-hotel/{hotel_id}",
    response_model=SuccessResponse[dict[str, bool]],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def recalculate_hotel(
    hotel_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
) -> SuccessResponse[dict[str, bool]]:
    """Queue one hotel score recalculation."""
    await AdminService(session).audit_action(
        actor.id, "hotel_scoring_started", "hotel", str(hotel_id)
    )
    dispatcher.recalculate_hotel(hotel_id)
    return success(request, {"queued": True})


@router.post(
    "/scoring/normalize",
    response_model=SuccessResponse[dict[str, bool]],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def normalize_scores(
    request: Request,
    session: SessionDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
    scope: ScopeType = ScopeType.GLOBAL,
    country_id: uuid.UUID | None = None,
    region_id: uuid.UUID | None = None,
    city_id: uuid.UUID | None = None,
    hotel_type: str | None = None,
    hotel_ids: list[uuid.UUID] = Query(default=[]),
) -> SuccessResponse[dict[str, bool]]:
    """Queue bell-curve normalization."""
    AdminService.validate_normalization_scope(
        scope, country_id, region_id, city_id, hotel_ids
    )
    await AdminService(session).audit_action(
        actor.id,
        "normalization_started",
        "scoring",
        metadata={
            "scope": scope.value,
            "country_id": str(country_id) if country_id else None,
            "region_id": str(region_id) if region_id else None,
            "city_id": str(city_id) if city_id else None,
            "hotel_type": hotel_type,
            "hotel_ids": [str(item) for item in hotel_ids],
        },
    )
    dispatcher.normalize(
        scope.value,
        country_id,
        region_id,
        city_id,
        hotel_type,
        hotel_ids or None,
    )
    return success(request, {"queued": True})


@router.get(
    "/data-quality/issues", response_model=SuccessResponse[list[DataQualityIssueRead]]
)
async def data_quality(
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
    issue_type: str | None = None,
    unresolved_only: bool = True,
    limit: int = Query(default=100, ge=1, le=500),
) -> SuccessResponse[list[DataQualityIssueRead]]:
    """List persisted quality issues."""
    return success(
        request,
        await AdminService(session).quality_issues(issue_type, unresolved_only, limit),
    )


@router.post(
    "/data-quality/scan",
    response_model=SuccessResponse[dict[str, bool]],
    dependencies=[Depends(rate_limit("admin_import"))],
)
async def scan_data_quality(
    request: Request,
    session: SessionDep,
    dispatcher: TaskDispatcherDep,
    actor: User = Depends(operator),
) -> SuccessResponse[dict[str, bool]]:
    """Queue an idempotent database consistency scan."""
    await AdminService(session).audit_action(
        actor.id, "data_quality_scan_started", "data_quality"
    )
    dispatcher.scan_data_quality()
    return success(request, {"queued": True})


@router.get("/dashboard", response_model=SuccessResponse[DashboardRead])
async def dashboard(
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
) -> SuccessResponse[DashboardRead]:
    """Return operational counters."""
    return success(request, await AdminService(session).dashboard())


@router.get("/system-health", response_model=SuccessResponse[dict[str, str]])
async def system_health(
    request: Request, _: User = Depends(operator)
) -> SuccessResponse[dict[str, str]]:
    """Return process-level status; full dependency status is exposed by readyz."""
    return success(request, {"status": "operational"})


@router.get("/users", response_model=ListResponse[UserProfile])
async def users(
    request: Request,
    session: SessionDep,
    _: User = Depends(admin_only),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
) -> ListResponse[UserProfile]:
    """List users for administrators."""
    data, pagination = await AdminService(session).users(limit, cursor)
    return listing(request, data, pagination)


@router.get("/users/{user_id}", response_model=SuccessResponse[UserProfile])
async def user(
    user_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    _: User = Depends(admin_only),
) -> SuccessResponse[UserProfile]:
    """Return one user for administrators."""
    return success(request, await AdminService(session).user(user_id))


@router.patch("/users/{user_id}/role", response_model=SuccessResponse[UserProfile])
async def update_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(admin_only),
) -> SuccessResponse[UserProfile]:
    """Update a user role and append an audit record."""
    return success(
        request, await UserService(session).change_role(actor.id, user_id, payload.role)
    )


@router.patch("/users/{user_id}/status", response_model=SuccessResponse[UserProfile])
async def update_status(
    user_id: uuid.UUID,
    payload: UserStatusUpdate,
    request: Request,
    session: SessionDep,
    actor: User = Depends(admin_only),
) -> SuccessResponse[UserProfile]:
    """Update account status and append an audit record."""
    return success(
        request,
        await UserService(session).change_status(actor.id, user_id, payload.status),
    )


@router.get(
    "/algorithm-versions", response_model=SuccessResponse[list[AlgorithmVersionRead]]
)
async def algorithm_versions(
    request: Request,
    session: SessionDep,
    _: User = Depends(operator),
) -> SuccessResponse[list[AlgorithmVersionRead]]:
    """List immutable scoring algorithm versions."""
    return success(request, await AdminService(session).algorithms())


@router.get("/settings", response_model=SuccessResponse[list[SystemSettingRead]])
async def system_settings(
    request: Request,
    session: SessionDep,
    _: User = Depends(admin_only),
) -> SuccessResponse[list[SystemSettingRead]]:
    """List non-secret system settings for administrators."""
    return success(request, await AdminService(session).settings())


@router.put(
    "/settings/{key}", response_model=SuccessResponse[SystemSettingRead]
)
async def update_system_setting(
    key: str,
    payload: SystemSettingWrite,
    request: Request,
    session: SessionDep,
    actor: User = Depends(admin_only),
) -> SuccessResponse[SystemSettingRead]:
    """Update a non-secret system setting and audit the action."""
    return success(
        request,
        await AdminService(session).update_setting(actor.id, key, payload.value),
    )


@router.post("/locations/cities", response_model=SuccessResponse[CityRead])
async def upsert_city(
    payload: CityWrite,
    request: Request,
    session: SessionDep,
    cache: CacheDep,
    actor: User = Depends(operator),
) -> SuccessResponse[CityRead]:
    """Create or update a canonical city used by hotel imports."""
    return success(
        request,
        await AdminService(session).upsert_city(actor.id, payload, cache),
    )
