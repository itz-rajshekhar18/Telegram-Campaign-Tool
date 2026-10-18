from __future__ import annotations

from datetime import date


def build_aida_variants(campaign, templates):
    product = campaign.product_name.strip()
    benefit = campaign.value_proposition.strip().rstrip(".")
    audience = campaign.audience.strip().rstrip(".")
    proof = campaign.proof.strip()
    offer = campaign.offer.strip()
    cta = campaign.call_to_action.strip()

    fields = {"product": product, "benefit": benefit, "audience": audience, "proof": proof, "offer": offer,
              "cta": cta, "info_bank": campaign.info_bank or "", "language": campaign.language or "", "tone": campaign.tone or ""}
    result = []
    for template in templates:
        item = {"label": template["label"]}
        for section in ("attention", "interest", "desire", "action"):
            value = template[section].format_map(fields).strip()
            if not value or len(value) > 1000:
                raise ValueError("Rendered draft sections must contain 1–1000 characters. Adjust the template or campaign brief.")
            item[section] = value
        item["action"] += "\n\nTo stop these updates, reply STOP."
        if len(item["action"]) > 1000:
            raise ValueError("Shorten the action template to leave room for STOP instructions.")
        result.append(item)
    return result


def quality_check(campaign, variants=None):
    objective = (campaign.objective or "").strip()
    audience = (campaign.audience or "").strip()
    metric = (campaign.primary_metric or "").strip()
    target = int(campaign.target_value or 0)
    deadline = (campaign.deadline or "").strip()
    try:
        deadline_is_future = date.fromisoformat(deadline) >= date.today()
    except ValueError:
        deadline_is_future = False
    has_value = bool((campaign.value_proposition or "").strip())
    checks = [
        {"name": "Specific", "passed": len(objective) >= 20 and bool(audience), "detail": "Objective and audience are described."},
        {"name": "Measurable", "passed": bool(metric) and target > 0, "detail": "A primary metric and numeric target are set."},
        {"name": "Achievable", "passed": bool(campaign.audience_size and target <= campaign.audience_size), "detail": "Target does not exceed the stated opted-in audience size; review delivery capacity as well."},
        {"name": "Relevant", "passed": has_value, "detail": "A concrete value proposition is provided."},
        {"name": "Time-bound", "passed": deadline_is_future, "detail": "A valid, current or future campaign deadline is set."},
    ]
    aida = []
    for variant in variants or []:
        aida.append({
            "label": variant.label,
            "score": sum(bool((getattr(variant, field, "") or "").strip()) for field in ("attention", "interest", "desire", "action")),
            "out_of": 4,
            "sections": {key: bool((getattr(variant, field, "") or "").strip()) for key, field in (("Attention", "attention"), ("Interest", "interest"), ("Desire", "desire"), ("Action", "action"))},
        })
    return {
        "smart": {"score": sum(item["passed"] for item in checks), "out_of": 5, "checks": checks},
        "consent": {"ready": bool(campaign.consent_confirmed and audience), "confirmed": bool(campaign.consent_confirmed), "audience_described": bool(audience), "detail": "Use this campaign only for people who explicitly opted in; honor STOP requests."},
        "aida": aida,
        "ready_to_review": bool(campaign.consent_confirmed and audience and variants),
    }


def optimize_results(variants, snapshots, primary_metric, settings):
    alert_percent = settings["opt_out_alert_percent"]
    comparison_min = settings["comparison_min_delivered"]
    results = []
    for variant in variants:
        rows = [row for row in snapshots if row.variant_id == variant.id]
        delivered = sum(row.delivered for row in rows)
        clicks = sum(row.clicks for row in rows)
        conversions = sum(row.conversions for row in rows)
        replies = sum(row.replies for row in rows)
        signups = sum(row.signups for row in rows)
        opt_outs = sum(row.opt_outs for row in rows)
        complaints = sum(row.complaints for row in rows)
        spend = sum(row.spend for row in rows)
        results.append({
            "variant_id": variant.id,
            "label": variant.label,
            "delivered": delivered,
            "clicks": clicks,
            "conversions": conversions,
            "replies": replies,
            "signups": signups,
            "opt_outs": opt_outs,
            "complaints": complaints,
            "spend": round(spend, 2),
            "click_rate": round(clicks / delivered * 100, 2) if delivered else 0,
            "conversion_rate": round(conversions / delivered * 100, 2) if delivered else 0,
            "cost_per_conversion": round(spend / conversions, 2) if conversions else None,
        })

    recommendations = []
    if not results or not any(row["delivered"] for row in results):
        recommendations.append("Record delivered counts and outcomes for each variant to get campaign-specific recommendations.")
    else:
        for row in results:
            if row["complaints"]:
                recommendations.append(f"Pause review of {row['label']}: {row['complaints']} complaint(s) were recorded. Check consent and message relevance.")
            if row["opt_outs"] and row["delivered"] and row["opt_outs"] / row["delivered"] * 100 >= alert_percent:
                recommendations.append(f"Review {row['label']}: opt-outs reached the configured {alert_percent:g}% alert. Recheck audience fit and frequency.")
        eligible = [row for row in results if row["delivered"] >= comparison_min]
        if len(eligible) >= 2:
            winner = max(eligible, key=lambda row: row[primary_metric] / row["delivered"])
            recommendations.append(f"{winner['label']} currently leads on {primary_metric} rate among variants with at least {comparison_min} delivered. Treat this as directional; keep testing before declaring a winner.")
        elif len(results) > 1:
            recommendations.append(f"Results are still low-volume. Your configured comparison floor is {comparison_min} delivered per variant, not a statistical significance guarantee.")
        if not recommendations:
            recommendations.append("No strong issue is visible in the recorded data. Compare variants again as more opted-in audience results arrive.")

    return {"variants": results, "recommendations": recommendations, "benchmark_note": f"Recommendations use recorded results and your workspace settings: {alert_percent:g}% opt-out alert and {comparison_min}-delivered comparison floor. These are configurable heuristics, not significance guarantees."}
