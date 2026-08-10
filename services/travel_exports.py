"""Safe, deterministic renderers for travel-plan exports and share pages."""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from agents.schemas import PlanDay, PlanItem, TravelPlan


def _text(value: object) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _safe_http_url(value: object) -> str:
    raw = _text(value)
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return raw


def _item_markdown(item: PlanItem) -> str:
    time_label = ""
    start = _text(item.start_time)
    end = _text(item.end_time)
    if start and end:
        time_label = f"{start}-{end} "
    elif start:
        time_label = f"{start} "
    location = _text(item.location) or _text(item.address)
    suffix = f" · {location}" if location else ""
    status = f"[{_text(item.status)}] " if _text(item.status) else ""
    detail = f"：{_text(item.detail)}" if _text(item.detail) else ""
    return f"- {time_label}{status}{_text(item.title)}{suffix}{detail}"


def render_plan_markdown(plan: TravelPlan) -> str:
    """Render only public structured plan fields; never model private state."""

    route = " -> ".join(filter(None, [_text(plan.origin), _text(plan.destination)])) or "旅行计划"
    lines = [
        f"# {route}",
        "",
        f"- 日期：{_text(plan.start_date)} ~ {_text(plan.end_date)}",
        f"- 人数：{int(plan.travelers or 1)}",
        f"- 时区：{_text(plan.timezone) or 'Asia/Shanghai'}",
    ]
    if plan.preferences:
        lines.append(f"- 偏好：{'、'.join(_text(item) for item in plan.preferences if _text(item))}")
    if _text(plan.summary):
        lines.extend(["", _text(plan.summary)])

    for day in plan.days:
        lines.extend(["", f"## {_text(day.date)} · {_text(day.title) or f'第{day.day_number}天'}"])
        if _text(day.summary):
            lines.append(_text(day.summary))
        if day.items:
            lines.extend(_item_markdown(item) for item in day.items)
        else:
            lines.append("- 暂无安排")

    if plan.transport_options:
        lines.extend(["", "## 交通候选"])
        for option in plan.transport_options:
            route_text = _text(option.summary)
            price = f" · {_text(option.price)}" if _text(option.price) else ""
            lines.append(
                f"- [{_text(option.mode)}] {_text(option.title)} · {_text(option.depart_date)} "
                f"{_text(option.depart_time)} -> {_text(option.arrive_date)} {_text(option.arrive_time)}"
                f"{price}{f' · {route_text}' if route_text else ''}"
            )

    notices = [
        ("风险", plan.risks),
        ("冲突", plan.conflicts),
        ("提醒", plan.alerts),
    ]
    for heading, entries in notices:
        clean_entries = [_text(entry) for entry in entries if _text(entry)]
        if clean_entries:
            lines.extend(["", f"## {heading}"])
            lines.extend(f"- {entry}" for entry in clean_entries)

    if plan.out_of_range_items:
        lines.extend(["", "## 日期范围外的已确认项目"])
        lines.extend(_item_markdown(item) for item in plan.out_of_range_items)

    sources = [source for source in plan.sources if _safe_http_url(source.url)]
    if sources:
        lines.extend(["", "## 来源"])
        for source in sources:
            title = _text(source.title) or _safe_http_url(source.url)
            lines.append(f"- [{title}]({_safe_http_url(source.url)})")

    lines.extend(["", f"> 计划版本 v{int(plan.version)} · 导出时间以下载时为准。"])
    return "\n".join(lines).strip() + "\n"


def _html_item(item: PlanItem) -> str:
    time_label = ""
    if _text(item.start_time) and _text(item.end_time):
        time_label = f"{_text(item.start_time)}–{_text(item.end_time)} · "
    elif _text(item.start_time):
        time_label = f"{_text(item.start_time)} · "
    location = _text(item.location) or _text(item.address)
    location_html = f"<span class=\"muted\">{escape(location)}</span>" if location else ""
    detail_html = f"<p>{escape(_text(item.detail))}</p>" if _text(item.detail) else ""
    return (
        "<li>"
        f"<strong>{escape(time_label + _text(item.title))}</strong> "
        f"<span class=\"status\">{escape(_text(item.status))}</span> {location_html}"
        f"{detail_html}"
        "</li>"
    )


def render_shared_plan_html(plan: TravelPlan, expires_at: str) -> str:
    """Render a self-contained read-only share page with escaped user data."""

    route = " → ".join(filter(None, [_text(plan.origin), _text(plan.destination)])) or "旅行计划"
    days_html = []
    for day in plan.days:
        items = "".join(_html_item(item) for item in day.items)
        if not items:
            items = "<li class=\"muted\">暂无安排</li>"
        days_html.append(
            "<section class=\"day\">"
            f"<h2>{escape(_text(day.date))} · {escape(_text(day.title) or f'第{day.day_number}天')}</h2>"
            f"<p class=\"summary\">{escape(_text(day.summary))}</p>"
            f"<ul>{items}</ul>"
            "</section>"
        )

    notices = []
    for heading, entries in (("风险", plan.risks), ("冲突", plan.conflicts), ("提醒", plan.alerts)):
        clean_entries = [_text(entry) for entry in entries if _text(entry)]
        if clean_entries:
            notices.append(
                f"<h2>{escape(heading)}</h2><ul>"
                + "".join(f"<li>{escape(entry)}</li>" for entry in clean_entries)
                + "</ul>"
            )

    preference_html = (
        f"<p class=\"muted\">偏好：{escape('、'.join(_text(item) for item in plan.preferences))}</p>"
        if plan.preferences
        else ""
    )
    notices_html = f'<section class="notices">{"".join(notices)}</section>' if notices else ""
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
  <title>{escape(route)} · AI Travel Agent</title>
  <style>
    :root {{ color-scheme: light; --ink:#163b38; --muted:#6d7e78; --paper:#f5f0e6; --card:#fffdf8; --line:#ded7c8; --accent:#c45f3c; }}
    * {{ box-sizing:border-box; }} body {{ margin:0; background:var(--paper); color:var(--ink); font:16px/1.7 system-ui,-apple-system,"Segoe UI",sans-serif; }}
    main {{ width:min(900px,100% - 32px); margin:32px auto 56px; }} header,.day,.notices {{ background:var(--card); border:1px solid var(--line); border-radius:18px; padding:24px; box-shadow:0 8px 28px rgba(30,50,40,.06); }}
    header {{ border-top:5px solid var(--accent); }} h1 {{ margin:0 0 8px; font-size:clamp(26px,5vw,42px); line-height:1.2; }} h2 {{ margin:0 0 8px; font-size:20px; }} .muted {{ color:var(--muted); }} .summary {{ margin:0 0 12px; color:var(--muted); }} .day {{ margin-top:16px; }} ul {{ margin:8px 0 0; padding-left:22px; }} li {{ margin:5px 0; }} li p {{ margin:2px 0 0; color:var(--muted); }} .status {{ color:var(--accent); font-size:12px; }} footer {{ margin-top:18px; color:var(--muted); font-size:13px; text-align:center; }}
  </style>
</head>
<body>
  <main>
    <header>
      <div class="muted">AI Travel Agent · 只读分享</div>
      <h1>{escape(route)}</h1>
      <div>{escape(_text(plan.start_date))} ~ {escape(_text(plan.end_date))} · {int(plan.travelers or 1)} 人</div>
      {preference_html}
      <p>{escape(_text(plan.summary))}</p>
    </header>
    {''.join(days_html)}
    {notices_html}
    <footer>分享链接有效期至 {escape(_text(expires_at))} · 计划版本 v{int(plan.version)}</footer>
  </main>
</body>
</html>"""
