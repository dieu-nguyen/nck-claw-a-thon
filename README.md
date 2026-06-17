# Dashboard Monitor — AI-Powered Fintech Monitoring Assistant

Stop staring at dashboards waiting for something to go wrong. Let the AI watch for you.

---

## What is it?

Dashboard Monitor is an AI agent that automatically checks your Superset dashboards every day, detects anomalies in key metrics, figures out what went wrong and why, then sends you a clear summary by email — all without you lifting a finger.

It's built for fintech operations teams who need to stay on top of payment success rates, transaction volumes, and bank-level performance, but don't have time to manually review dashboards every morning.

---

## What can it do?

### Monitors multiple flows in parallel
Run multiple monitoring checks at the same time. Each check gets its own live box in the UI, streaming progress as it works. No waiting for one to finish before the other starts.

### Detects anomalies automatically
The agent knows your thresholds — success rate targets, volume baselines, weekend vs. weekday patterns. It flags issues the moment they cross the line, not when someone notices.

### Tells you *why*, not just *what*
When something goes wrong, the agent doesn't just say "SR dropped." It deep-dives into the data, identifies whether it's an isolated bank issue or a platform-wide failure, ranks the top suspects by impact, and explains its reasoning in plain language.

### Sends a structured email report
Every run sends one clean, actionable email — whether everything is normal (heartbeat) or something needs attention. Subject lines are designed for easy inbox filtering.

### Links directly to the relevant dashboard
Each result box links straight to the Superset dashboard it analyzed, so you can jump to the source data in one click.

---

## How it works

1. **You hit Run** in the web UI (or schedule it to run automatically)
2. **The AI agent reads your Superset dashboards** — it searches for the right dashboard, lists the charts, and fetches the data
3. **It analyzes the data** against baselines and thresholds, step by step
4. **It sends you an email** with a summary, key metrics, and recommended actions
5. **The result appears in the UI** — color-coded by severity, with full step-by-step log you can expand if you want to see how it reached its conclusion

The whole process takes 2–3 minutes per flow.

---

## Currently monitoring

| Flow | What it watches |
|------|----------------|
| **Bank Link SR** | Daily success rate for bank linking — detects drops, SLA breaches, and isolates which banks are causing issues |
| **Transaction Volume** | Daily transaction counts, amounts, and SR by bank and payment type (Thanh toán, Nạp Tiền, Rút tiền, Chuyển tiền) — detects volume anomalies and SR degradation |

Adding a new monitoring flow is as simple as writing a new prompt file describing what to check. No code changes needed.

---

## What you get in the email

```
【TÌNH TRẠNG】 Cảnh báo
【TÓM TẮT】 SR tổng hôm nay đạt 93.2%, giảm 2.8 điểm % so với hôm qua.
            Phát hiện lỗi cục bộ tại Agribank và BIDV.

【KEY METRICS】
- SR tổng: 93.2% (Δ -2.8% so với hôm qua, baseline 96.5%)
- Lượt liên kết: 58,420 | Thành công: 54,448 | Thất bại: 3,972
- NH dưới SLA: 3/10 | SR thấp nhất: 88.4% (Agribank)

【PHÂN TÍCH】
- Phân loại: LỖI CỤC BỘ — 2 ngân hàng nghi phạm chính
  1) Agribank: SR 88.4% (Δ -6.1%), failed 300, kéo SR tổng -0.8đ%
  2) BIDV: SR 91.5% (Δ -3.2%), failed 550, kéo SR tổng -0.6đ%

【ĐỀ XUẤT HÀNH ĐỘNG】
- Kiểm tra log kết nối tới Agribank và BIDV
- Liên hệ đầu mối kỹ thuật hai ngân hàng này
- Theo dõi sát trong 2 giờ tới
```

---

## Why use it

- **Saves time** — no more manual dashboard checks every morning
- **Catches issues faster** — the agent runs on schedule, not when someone remembers to look
- **Reduces alert fatigue** — one clean email per run, not a flood of raw metric notifications
- **Explains root cause** — distinguishes isolated bank issues from platform-wide failures so the right team gets the right alert
- **Easy to extend** — add a new monitoring flow by describing what to check in plain language, no engineering needed
