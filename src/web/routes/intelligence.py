"""Production Intelligence, Resource Tracking & Operational Analytics Web Routes (Task 3.17).

Provides:
- GET /intelligence: Main bilingual dashboard for resource tracking, costs, and insights.
- POST /intelligence/flow-credits: Manual credit ledger transaction recording.
- POST /intelligence/cost-config: Update channel production cost unit rates.
- GET /intelligence/report/json: Download safe JSON production report.
- GET /intelligence/report/csv: Download safe CSV production report.
- GET /intelligence/api/summary: JSON API summary for dashboard widgets.
"""

from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, HTTPException, Response
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from src.database.models import FlowCreditTransactionCreate, ProductionCostConfigUpdate
from src.core.logger import get_logger
from src.web.dependencies import ServiceContainer, get_service_container

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")
logger = get_logger("studio.web.routes.intelligence")


@router.get("/intelligence", response_class=HTMLResponse)
async def intelligence_dashboard_view(
    request: Request,
    channel_id: Optional[str] = None,
    project_id: Optional[str] = None,
    services: ServiceContainer = Depends(get_service_container),
):
    """Render the primary Production Intelligence dashboard."""
    channels = services.channel_service.list_channels(include_inactive=True)

    # Resolve active channel
    active_channel = None
    if channel_id:
        for ch in channels:
            if ch.id == channel_id.strip():
                active_channel = ch
                break

    if not active_channel and channels:
        active_channel = channels[0]

    ch_id = active_channel.id if active_channel else ""

    # Projects under channel
    projects = []
    if ch_id:
        try:
            projects = services.project_service.list_projects(ch_id)
        except Exception:
            projects = []

    # Gather intelligence metrics
    storage_info = services.intelligence_service.get_channel_storage_breakdown(ch_id) if ch_id else {}
    render_analytics = services.intelligence_service.get_render_analytics(ch_id if ch_id else None)
    cost_info = services.intelligence_service.calculate_costs(ch_id) if ch_id else {}
    flow_credits = services.intelligence_service.get_flow_credit_summary(ch_id) if ch_id else {}
    insights = services.intelligence_service.generate_deterministic_insights(ch_id) if ch_id else []
    system_resources = services.intelligence_service.get_system_resources()
    history = services.intelligence_service.get_unified_production_history(
        ch_id, project_id=project_id or None, limit=40
    ) if ch_id else []

    error_msg = request.query_params.get("error")
    success_msg = request.query_params.get("success")

    return templates.TemplateResponse(
        request=request,
        name="intelligence.html",
        context={
            "channels": channels,
            "active_channel": active_channel,
            "selected_channel_id": ch_id,
            "selected_project_id": project_id or "",
            "projects": projects,
            "storage_info": storage_info,
            "render_analytics": render_analytics,
            "cost_info": cost_info,
            "flow_credits": flow_credits,
            "insights": insights,
            "system_resources": system_resources,
            "history": history,
            "error_msg": error_msg,
            "success_msg": success_msg,
        },
    )


@router.post("/intelligence/flow-credits")
async def record_flow_credits(
    request: Request,
    channel_id: str = Form(...),
    credits_amount: float = Form(...),
    transaction_type: str = Form("topup"),
    operator_note: Optional[str] = Form(None),
    project_id: Optional[str] = Form(None),
    services: ServiceContainer = Depends(get_service_container),
):
    """Log a manual Google Flow credit ledger entry."""
    clean_ch = channel_id.strip()
    try:
        tx = services.intelligence_service.record_flow_credit_entry(
            channel_id=clean_ch,
            payload=FlowCreditTransactionCreate(
                channel_id=clean_ch,
                project_id=project_id.strip() if project_id else None,
                transaction_type=transaction_type.strip(),
                credits_amount=credits_amount,
                operator_note=operator_note.strip() if operator_note else None,
                source="manual_user_entry",
            ),
        )

        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"status": "success", "transaction": tx.model_dump()})

        return RedirectResponse(
            url=f"/intelligence?channel_id={clean_ch}&success=Credit+entry+recorded+successfully",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Failed recording Flow credits for '{clean_ch}': {e}")
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)
        return RedirectResponse(
            url=f"/intelligence?channel_id={clean_ch}&error=Failed+recording+credit:+{str(e)}",
            status_code=303,
        )


@router.post("/intelligence/cost-config")
async def update_cost_config(
    request: Request,
    channel_id: str = Form(...),
    cost_per_render_second: float = Form(0.0),
    cost_per_storage_gb_month: float = Form(0.0),
    cost_per_api_take: float = Form(0.0),
    currency: str = Form("USD"),
    services: ServiceContainer = Depends(get_service_container),
):
    """Update channel production cost unit rates."""
    clean_ch = channel_id.strip()
    try:
        cfg = services.intelligence_service.update_cost_config(
            channel_id=clean_ch,
            updates=ProductionCostConfigUpdate(
                cost_per_render_second=cost_per_render_second,
                cost_per_storage_gb_month=cost_per_storage_gb_month,
                cost_per_api_take=cost_per_api_take,
                currency=currency.strip().upper(),
            ),
        )

        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"status": "success", "config": cfg.model_dump()})

        return RedirectResponse(
            url=f"/intelligence?channel_id={clean_ch}&success=Cost+configuration+updated",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Failed updating cost config for '{clean_ch}': {e}")
        accept = request.headers.get("accept", "")
        if "application/json" in accept:
            return JSONResponse({"status": "error", "error": str(e)}, status_code=400)
        return RedirectResponse(
            url=f"/intelligence?channel_id={clean_ch}&error=Failed+updating+config:+{str(e)}",
            status_code=303,
        )


@router.get("/intelligence/report/json")
async def download_json_report(
    channel_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    """Generate and download a comprehensive JSON report safely without Path Traversal."""
    clean_ch = channel_id.strip()
    filename, content = services.intelligence_service.export_production_report(clean_ch, "json")
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/intelligence/report/csv")
async def download_csv_report(
    channel_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    """Generate and download a comprehensive CSV report safely without Path Traversal."""
    clean_ch = channel_id.strip()
    filename, content = services.intelligence_service.export_production_report(clean_ch, "csv")
    return Response(
        content=content,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/intelligence/report/markdown")
async def download_markdown_report(
    channel_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    """Generate and download a comprehensive Markdown report safely without Path Traversal."""
    clean_ch = channel_id.strip()
    filename, content = services.intelligence_service.export_production_report(clean_ch, "markdown")
    return Response(
        content=content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/intelligence/api/summary")
async def get_intelligence_summary_json(
    channel_id: str,
    services: ServiceContainer = Depends(get_service_container),
):
    """JSON API endpoint returning high-level summary KPIs for widgets."""
    clean_ch = channel_id.strip()
    storage_info = services.intelligence_service.get_channel_storage_breakdown(clean_ch)
    render_analytics = services.intelligence_service.get_render_analytics(clean_ch)
    cost_info = services.intelligence_service.calculate_costs(clean_ch)
    flow_credits = services.intelligence_service.get_flow_credit_summary(clean_ch)
    insights = services.intelligence_service.generate_deterministic_insights(clean_ch)

    return JSONResponse({
        "channel_id": clean_ch,
        "storage": {
            "total_bytes": storage_info["total_bytes"],
            "human_size": storage_info["human_size"],
            "total_gb": storage_info["total_gb"],
        },
        "renders": {
            "total": render_analytics["total_renders"],
            "completed": render_analytics["completed_count"],
            "failed": render_analytics["failed_count"],
            "success_rate": render_analytics["success_rate_percent"],
            "avg_duration": render_analytics["timing"]["avg_formatted"],
        },
        "costs": {
            "actual_known": cost_info["actual_known_cost"],
            "estimated": cost_info["estimated_cost"],
            "currency": cost_info["currency"],
        },
        "flow_credits": {
            "balance": flow_credits["current_balance"],
            "total_topup": flow_credits["total_topup"],
            "attribution": flow_credits["attribution"],
        },
        "insights_count": len(insights),
    })
