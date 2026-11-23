"""Production Intelligence CLI Tool (Task 3.17).

Provides CLI commands for:
- Viewing production resource & cost summary.
- Exporting production intelligence reports (JSON / CSV).
- Logging manual Google Flow credit ledger transactions.
- Updating channel production cost unit rates.
"""

import argparse
import sys
import json
from pathlib import Path

from src.database.connection import DatabaseManager
from src.database.models import FlowCreditTransactionCreate, ProductionCostConfigUpdate
from src.storage.service import StorageService
from src.services.intelligence_service import IntelligenceService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli.intelligence",
        description="Universal AI Video Studio - Production Intelligence & Resource Tracking CLI",
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # 1. Summary
    p_summary = subparsers.add_parser("summary", help="Display intelligence summary for a channel.")
    p_summary.add_argument("--channel", "-c", required=True, help="Channel ID")
    p_summary.add_argument("--power-watts", type=float, default=None, help="Optional host power draw in Watts")
    p_summary.add_argument("--kwh-price", type=float, default=None, help="Optional electricity price per kWh")

    # 2. Export Report
    p_export = subparsers.add_parser("export", help="Export comprehensive production report.")
    p_export.add_argument("--channel", "-c", required=True, help="Channel ID")
    p_export.add_argument("--format", "-f", choices=["json", "csv", "md", "markdown"], default="json", help="Export format")
    p_export.add_argument("--output", "-o", help="Optional local output file path")

    # 3. Log Flow Credits
    p_credit = subparsers.add_parser("credit", help="Log manual Google Flow credit transaction.")
    p_credit.add_argument("--channel", "-c", required=True, help="Channel ID")
    p_credit.add_argument("--amount", "-a", type=float, required=True, help="Credit amount (e.g. +100 or -15)")
    p_credit.add_argument("--type", "-t", choices=["topup", "usage", "adjustment"], default="topup", help="Type")
    p_credit.add_argument("--note", "-n", default="Manual CLI Entry", help="Operator note")
    p_credit.add_argument("--project", "-p", help="Optional project ID")

    # 4. Set Cost Config
    p_cost = subparsers.add_parser("cost", help="Configure unit cost rates for channel.")
    p_cost.add_argument("--channel", "-c", required=True, help="Channel ID")
    p_cost.add_argument("--render-rate", type=float, help="Cost per render second")
    p_cost.add_argument("--storage-rate", type=float, help="Cost per storage GB/month")
    p_cost.add_argument("--take-rate", type=float, help="Cost per Flow API take")
    p_cost.add_argument("--currency", help="Currency code (e.g. USD, EUR, SAR)")

    return parser


def main(args=None, db_manager=None, storage_service=None) -> int:
    parser = build_parser()
    opts = parser.parse_args(args)

    db_mgr = db_manager or DatabaseManager()
    storage = storage_service or StorageService()
    service = IntelligenceService(db_manager=db_mgr, storage_service=storage)

    if opts.subcommand == "summary":
        storage_data = service.get_channel_storage_breakdown(opts.channel)
        render_data = service.get_render_analytics(opts.channel)
        cost_data = service.calculate_costs(opts.channel)
        cost_summary = service.get_cost_summary(
            opts.channel,
            power_consumption_watts=opts.power_watts,
            electricity_price_kwh=opts.kwh_price,
        )
        flow_data = service.get_flow_credit_summary(opts.channel)
        insights = service.generate_deterministic_insights(opts.channel)

        print("=" * 65)
        print(f" PRODUCTION INTELLIGENCE SUMMARY: {opts.channel}")
        print("=" * 65)
        print(f" Storage Footprint : {storage_data['human_size']} ({storage_data['total_gb']} GB)")
        print(f" Render Success %  : {render_data['success_rate_percent']}% ({render_data['completed_count']}/{render_data['total_renders']} completed)")
        print(f" Average Render    : {render_data['timing']['avg_formatted']}")
        print(f" Flow Credits      : {flow_data['current_balance']} (Attribution: {flow_data['attribution']})")
        print(f" Actual Known Cost : {cost_summary['actual_known_cost']['label']}")
        print(f" Estimated Cost    : {cost_summary['estimated_production_cost']['label']}")
        print(f" Electricity Cost  : {cost_summary['energy_cost']['label']}")
        print(f" Active Insights   : {len(insights)}")
        for ins in insights:
            print(f"   [{ins['severity'].upper()}] {ins['title_en']}: {ins['message_en']}")
        print("=" * 65)
        return 0

    elif opts.subcommand == "export":
        filename, content = service.export_production_report(opts.channel, opts.format)
        target_path = Path(opts.output) if opts.output else Path(filename)
        target_path.write_text(content, encoding="utf-8")
        print(f"Exported {opts.format.upper()} report to: {target_path.resolve()} ({len(content)} chars)")
        return 0

    elif opts.subcommand == "credit":
        tx = service.record_flow_credit_entry(
            channel_id=opts.channel,
            payload=FlowCreditTransactionCreate(
                channel_id=opts.channel,
                project_id=opts.project,
                transaction_type=opts.type,
                credits_amount=opts.amount,
                operator_note=opts.note,
                source="manual_user_entry",
            ),
        )
        print(f"Recorded credit entry '{tx.id}': delta={tx.credits_amount:+.2f}, new_balance={tx.balance_after:.2f}")
        return 0

    elif opts.subcommand == "cost":
        cfg = service.update_cost_config(
            channel_id=opts.channel,
            updates=ProductionCostConfigUpdate(
                cost_per_render_second=opts.render_rate,
                cost_per_storage_gb_month=opts.storage_rate,
                cost_per_api_take=opts.take_rate,
                currency=opts.currency,
            ),
        )
        print(f"Updated cost rates for '{opts.channel}': render={cfg.cost_per_render_second}/s, storage={cfg.cost_per_storage_gb_month}/GB, take={cfg.cost_per_api_take}/take ({cfg.currency})")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
