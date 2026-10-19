#!/usr/bin/env python3
"""
benchmark_spam.py - synthetic spam/ham benchmark for the local classifier.

Generates a labeled fake mailbox (default: 500 spam + 150 ham), classifies
every message with the same per-email Ollama call used by llm_spam_scan.py,
and reports accuracy, precision, recall, F1, and false-positive rate.

No real email or network mailbox is touched.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from llm_spam_scan import (
    DEFAULT_MODEL,
    DEFAULT_OLLAMA,
    PROMPT_VERSION,
    EmailMeta,
    OllamaClient,
    OllamaError,
    ContextPressure,
)

FIRST_NAMES = ["Alice", "Marco", "Priya", "Daniel", "Sofia", "Liam", "Nadia", "Tom", "Elena", "Jonas"]
LAST_NAMES = ["Rossi", "Chen", "Khan", "Miller", "Garcia", "Novak", "Silva", "Brown", "Kim", "Weber"]
CITIES = ["Milan", "Berlin", "Austin", "Toronto", "Lisbon", "Osaka", "Dublin", "Seattle"]
BANKS = ["Chase", "Wells Fargo", "Bank of America", "Citi", "HSBC"]
STORES = ["Amazon", "IKEA", "Decathlon", "Zara", "Booking.com", "Spotify"]
COURIERS = ["DHL", "UPS", "FedEx", "Poste Italiane"]


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _handle(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES).lower()}.{rng.choice(LAST_NAMES).lower()}{rng.randint(1, 99)}"


def _auth(pass_auth: bool, domain: str) -> str:
    if pass_auth:
        return (
            f"mx.google.com; spf=pass smtp.mailfrom={domain}; "
            f"dkim=pass header.d={domain}; dmarc=pass header.from={domain}"
        )
    return (
        f"mx.google.com; spf=fail smtp.mailfrom={domain}; "
        f"dkim=none; dmarc=fail header.from={domain}"
    )


# --------------------------------------------------------------------------- #
# Spam templates
# --------------------------------------------------------------------------- #

def spam_bank(rng, i):
    bank = rng.choice(BANKS)
    domain = rng.choice(["secure-alerts-notice.com", "account-verify-center.net", "banking-support.live"])
    return EmailMeta(
        from_=f"{bank} Security <security@{domain}>",
        reply_to=f"recover@{domain}",
        subject=rng.choice([
            "Your account has been suspended",
            "Unusual sign-in attempt - action required",
            "Verify your identity within 24 hours",
        ]),
        date="Mon, 19 Oct 2026 08:12:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"We detected unusual activity on your {bank} account. Confirm your password and card details at https://example.invalid/{bank.lower()} or your account will be locked.",
    )


def spam_crypto(rng, i):
    domain = rng.choice(["btc-giveaway.io", "eth-rewards.co", "crypto-airdrop.vip"])
    amount = rng.choice(["2.5 BTC", "18 ETH", "50,000 USDT"])
    return EmailMeta(
        from_=f"Crypto Rewards <winner@{domain}>",
        subject=f"You have been selected to receive {amount}",
        date="Mon, 19 Oct 2026 09:01:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"Congratulations! Your wallet was randomly selected for {amount}. Claim within 12 hours by connecting your wallet at https://example.invalid/claim and paying the network fee.",
    )


def spam_pharmacy(rng, i):
    domain = rng.choice(["meds-direct.biz", "cheap-pharma.shop", "rx-no-script.net"])
    return EmailMeta(
        from_=f"Online Pharmacy <deals@{domain}>",
        subject=rng.choice(["80% off prescription meds", "No prescription required", "Cheap pills, discreet shipping"]),
        date="Mon, 19 Oct 2026 07:45:00 +0000",
        auth_results=_auth(False, domain),
        list_unsubscribe=f"<mailto:unsub@{domain}>",
        snippet="Buy painkillers, anxiety meds and more without a prescription. Discreet packaging, worldwide delivery, 80% discount this week only.",
    )


def spam_invoice(rng, i):
    domain = rng.choice(["billing-desk.co", "invoice-portal.click", "accounts-pay.live"])
    amount = rng.choice(["$1,284.00", "€2,950.00", "£740.00"])
    return EmailMeta(
        from_=f"Accounts Receivable <billing@{domain}>",
        reply_to=f"payments@{domain}",
        subject=f"Overdue invoice #{rng.randint(10000, 99999)} - {amount}",
        date="Mon, 19 Oct 2026 06:20:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"Please find attached invoice for {amount}, now overdue. Open the attached PDF and settle immediately to avoid legal action.",
    )


def spam_delivery(rng, i):
    courier = rng.choice(COURIERS)
    domain = rng.choice(["parcel-redelivery.info", "shipping-fee.center", "track-package.live"])
    return EmailMeta(
        from_=f"{courier} Delivery <notice@{domain}>",
        subject=f"Your {courier} parcel is on hold - unpaid fee",
        date="Mon, 19 Oct 2026 10:15:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"Your parcel could not be delivered. A customs fee of {rng.choice(['$1.99','€2.50','£1.75'])} is required. Pay at https://example.invalid/track to reschedule within 48 hours.",
    )


def spam_lottery(rng, i):
    domain = rng.choice(["intl-lottery.org", "prize-claim.net", "winner-notice.co"])
    return EmailMeta(
        from_=f"International Lottery <claims@{domain}>",
        subject="Congratulations! You are a winner",
        date="Mon, 19 Oct 2026 05:30:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"You have won {rng.choice(['$850,000','€1,200,000','£500,000'])} in the international lottery. Send your name, address and bank details to claim.",
    )


def spam_techsupport(rng, i):
    domain = rng.choice(["support-alert.live", "windows-security.help", "virus-removal.center"])
    return EmailMeta(
        from_=f"Security Center <alert@{domain}>",
        subject="Your computer is infected with 5 viruses",
        date="Mon, 19 Oct 2026 11:02:00 +0000",
        auth_results=_auth(False, domain),
        snippet="Your Windows device is infected. Call our support line immediately and allow remote access so a technician can remove the malware. Do not restart your computer.",
    )


def spam_romance(rng, i):
    domain = rng.choice(["lonely-hearts.me", "find-love.today", "military-connect.org"])
    return EmailMeta(
        from_=f"{_name(rng)} <{_handle(rng)}@{domain}>",
        subject="I found your profile and would love to talk",
        date="Mon, 19 Oct 2026 04:10:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"Hello dear, I am {_name(rng)}, a military officer stationed abroad. I saw your photo and felt a connection. Please reply so we can talk privately.",
    )


def spam_seo(rng, i):
    domain = rng.choice(["seo-rank-pro.agency", "backlinks-4u.biz", "google-ranking.expert"])
    return EmailMeta(
        from_=f"SEO Growth Team <sales@{domain}>",
        subject="Your website is invisible on Google",
        date="Mon, 19 Oct 2026 03:00:00 +0000",
        auth_results=_auth(False, domain),
        list_unsubscribe=f"<mailto:unsub@{domain}>",
        snippet="We guarantee page 1 ranking in 30 days with 10,000 high-quality backlinks. Reply for a free audit and pricing.",
    )


def spam_tax(rng, i):
    domain = rng.choice(["tax-refund-portal.co", "revenue-service.live", "gov-refund.info"])
    return EmailMeta(
        from_=f"Tax Refund Service <refund@{domain}>",
        subject=f"Your tax refund of {rng.choice(['$1,240','€980','£1,510'])} is waiting",
        date="Mon, 19 Oct 2026 12:00:00 +0000",
        auth_results=_auth(False, domain),
        snippet="You are eligible for a refund. Submit your social security number, date of birth and bank account details within 24 hours to receive the payment.",
    )


def spam_subscription(rng, i):
    domain = rng.choice(["norton-renewals.co", "antivirus-billing.net", "subscription-support.live"])
    amount = rng.choice(["$399.99", "$249.00", "€299.00"])
    return EmailMeta(
        from_=f"Subscription Billing <support@{domain}>",
        subject=f"Your subscription auto-renewed for {amount}",
        date="Mon, 19 Oct 2026 09:40:00 +0000",
        auth_results=_auth(False, domain),
        snippet=f"Your antivirus subscription was renewed for {amount}. If you did not authorize this charge, call our cancellation line immediately to receive a refund.",
    )


def spam_ceo(rng, i):
    domain = rng.choice(["company-exec.net", "ceo-message.co", "urgent-request.live"])
    return EmailMeta(
        from_=f"{_name(rng)} <ceo@{domain}>",
        reply_to=f"{_handle(rng)}@{domain}",
        subject="Quick confidential request",
        date="Mon, 19 Oct 2026 08:55:00 +0000",
        auth_results=_auth(False, domain),
        snippet="I am in a meeting and need a favor. Please purchase four $500 gift cards and send me the codes. Keep this confidential and do not call.",
    )


def spam_loan(rng, i):
    domain = rng.choice(["instant-cash.today", "loan-approval.live", "fast-money.biz"])
    return EmailMeta(
        from_=f"Lending Desk <offers@{domain}>",
        subject="You are pre-approved for a $20,000 loan",
        date="Mon, 19 Oct 2026 02:25:00 +0000",
        auth_results=_auth(False, domain),
        list_unsubscribe=f"<mailto:unsub@{domain}>",
        snippet="No credit check, no collateral, funds in 24 hours. Reply with your ID and bank details to lock in your rate.",
    )


def spam_watches(rng, i):
    domain = rng.choice(["luxury-replicas.co", "watch-outlet.vip", "premium-copies.net"])
    return EmailMeta(
        from_=f"Luxury Outlet <sales@{domain}>",
        subject="Rolex-quality watches from $89",
        date="Mon, 19 Oct 2026 01:10:00 +0000",
        auth_results=_auth(False, domain),
        list_unsubscribe=f"<mailto:unsub@{domain}>",
        snippet="Premium replica watches, exact 1:1 quality, free worldwide shipping and a 5-year warranty. Limited stock.",
    )


SPAM_TEMPLATES = [
    spam_bank, spam_crypto, spam_pharmacy, spam_invoice, spam_delivery,
    spam_lottery, spam_techsupport, spam_romance, spam_seo, spam_tax,
    spam_subscription, spam_ceo, spam_loan, spam_watches,
]


# --------------------------------------------------------------------------- #
# Ham templates
# --------------------------------------------------------------------------- #

def ham_personal(rng, i):
    name = _name(rng)
    domain = "gmail.com"
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject=rng.choice([
            "Dinner on Saturday?",
            "Photos from the weekend",
            "Can you send me the address?",
            "Happy birthday!",
        ]),
        date="Mon, 19 Oct 2026 08:30:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Hey! Are you free {rng.choice(['Saturday','Sunday','Friday'])} evening? A few of us are meeting in {rng.choice(CITIES)} around 8. Let me know if you can make it.",
    )


def ham_work(rng, i):
    domain = rng.choice(["acme-corp.com", "northwind.example", "contoso.example"])
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject=rng.choice([
            "Notes from today's planning meeting",
            "Re: Q4 budget review",
            "Updated deck for the client call",
            "Lunch with the new hire",
        ]),
        date="Mon, 19 Oct 2026 09:10:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Hi, I attached the notes from today's meeting and the updated timeline. Could you review the budget section before Thursday's call? Thanks, {name.split()[0]}",
    )


def ham_receipt(rng, i):
    store = rng.choice(STORES)
    domain = f"{store.lower().replace(' ', '').split('.')[0]}.com"
    return EmailMeta(
        from_=f"{store} <orders@{domain}>",
        subject=f"Your {store} order confirmation",
        date="Mon, 19 Oct 2026 10:01:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Thanks for your order. Order #{rng.randint(100000,999999)} has been confirmed and will ship within 2 business days. You can view your receipt in your account.",
    )


def ham_shipping(rng, i):
    store = rng.choice(STORES)
    domain = f"{store.lower().replace(' ', '').split('.')[0]}.com"
    return EmailMeta(
        from_=f"{store} Delivery <shipping@{domain}>",
        subject=f"Your {store} order has shipped",
        date="Mon, 19 Oct 2026 11:20:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Good news: your order is on its way. Tracking number 1Z{rng.randint(100000000,999999999)}. Estimated delivery: {rng.choice(['Tuesday','Wednesday','Thursday'])}.",
    )


def ham_newsletter(rng, i):
    brand = rng.choice(["Stratechery", "The Pragmatic Engineer", "Lenny's Newsletter", "Import AI"])
    domain = "substack.com" if brand != "Stratechery" else "stratechery.com"
    return EmailMeta(
        from_=f"{brand} <hello@{domain}>",
        subject=rng.choice([
            "This week in product and engineering",
            "The state of AI tooling",
            "Five lessons from scaling a team",
        ]),
        date="Mon, 19 Oct 2026 07:00:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="In this issue: why smaller teams are shipping faster, a practical guide to evaluation-driven development, and three links worth your time.",
    )


def ham_calendar(rng, i):
    name = _name(rng)
    domain = "google.com"
    return EmailMeta(
        from_=f"Google Calendar <calendar-notification@{domain}>",
        subject=f"Invitation: {rng.choice(['Product sync','1:1','Design review','All hands'])}",
        date="Mon, 19 Oct 2026 08:00:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"{name} invited you to an event on {rng.choice(['Tuesday','Wednesday'])} at 10:00. Location: Meeting Room {rng.randint(1,12)}. Accept or decline in Google Calendar.",
    )


def ham_github(rng, i):
    domain = "github.com"
    repo = rng.choice(["acme/api", "acme/web", "acme/infra"])
    return EmailMeta(
        from_=f"GitHub <notifications@{domain}>",
        subject=f"[{repo}] Pull request #{rng.randint(100,999)} review requested",
        date="Mon, 19 Oct 2026 12:30:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://github.com/notifications/unsubscribe/{rng.randint(1,999)}>",
        snippet=f"You were requested to review a pull request in {repo}. Changes: update dependency versions and fix a flaky test.",
    )


def ham_bank(rng, i):
    bank = rng.choice(BANKS)
    domain = f"{bank.lower().replace(' ', '')}.com"
    return EmailMeta(
        from_=f"{bank} Alerts <alerts@{domain}>",
        subject=rng.choice(["Your monthly statement is ready", "A new sign-in to your account", "Payment scheduled"]),
        date="Mon, 19 Oct 2026 06:45:00 +0000",
        auth_results=_auth(True, domain),
        snippet="This is a service message about your account. Sign in through your bank's official app or website to view the details. We never ask for passwords by email.",
    )


def ham_travel(rng, i):
    domain = rng.choice(["airline.example", "booking.com", "hotels.example"])
    city = rng.choice(CITIES)
    return EmailMeta(
        from_=f"Travel Confirmations <bookings@{domain}>",
        subject=f"Your trip to {city} is confirmed",
        date="Mon, 19 Oct 2026 13:15:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Booking reference ABC{rng.randint(1000,9999)}. Your flight and hotel in {city} are confirmed. Check-in opens 24 hours before departure.",
    )


def ham_utility(rng, i):
    provider = rng.choice(["City Power", "Metro Water", "FiberNet"])
    domain = f"{provider.lower().replace(' ', '')}.example"
    amount = rng.choice(["$84.20", "€62.10", "£57.40"])
    return EmailMeta(
        from_=f"{provider} Billing <billing@{domain}>",
        subject=f"Your bill of {amount} is available",
        date="Mon, 19 Oct 2026 05:50:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Your monthly statement is ready. Amount due: {amount}. Payment will be collected automatically on the 28th. View the full statement in your account.",
    )


def ham_promo(rng, i):
    store = rng.choice(STORES)
    domain = f"{store.lower().replace(' ', '').split('.')[0]}.com"
    return EmailMeta(
        from_=f"{store} <deals@{domain}>",
        subject=rng.choice(["20% off this weekend", "Your member offer ends soon", "New arrivals for autumn"]),
        date="Mon, 19 Oct 2026 09:25:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet=f"You are receiving this because you subscribed to {store} updates. Enjoy 20% off selected items this weekend with your member account.",
    )


def ham_security(rng, i):
    domain = rng.choice(["accounts.google.com", "microsoft.com", "github.com"])
    return EmailMeta(
        from_=f"Account Security <no-reply@{domain}>",
        subject=rng.choice(["New sign-in to your account", "Password changed successfully", "Security checkup reminder"]),
        date="Mon, 19 Oct 2026 14:05:00 +0000",
        auth_results=_auth(True, domain),
        snippet="We noticed a new sign-in from a device you have used before. If this was you, no action is needed. If not, review your active sessions from the official account page.",
    )


def ham_school(rng, i):
    name = _name(rng)
    domain = "school.example"
    return EmailMeta(
        from_=f"{name} <office@{domain}>",
        subject=rng.choice(["Parent-teacher meeting next week", "Field trip permission form", "School closure reminder"]),
        date="Mon, 19 Oct 2026 08:20:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Dear parents, this is a reminder about the {rng.choice(['science fair','parent-teacher meeting','field trip'])} next week. Please complete the form in the parent portal by Friday.",
    )


HAM_TEMPLATES = [
    ham_personal, ham_work, ham_receipt, ham_shipping, ham_newsletter,
    ham_calendar, ham_github, ham_bank, ham_travel, ham_utility,
    ham_promo, ham_security, ham_school,
]


@dataclass
class Sample:
    label: str
    meta: EmailMeta


def make_dataset(spam_count: int, ham_count: int, seed: int = 42) -> list[Sample]:
    rng = random.Random(seed)
    samples: list[Sample] = []
    for index in range(spam_count):
        template = SPAM_TEMPLATES[index % len(SPAM_TEMPLATES)]
        samples.append(Sample("spam", template(rng, index)))
    for index in range(ham_count):
        template = HAM_TEMPLATES[index % len(HAM_TEMPLATES)]
        samples.append(Sample("ham", template(rng, index)))
    rng.shuffle(samples)
    for index, sample in enumerate(samples):
        sample.meta.uid = str(index + 1)
        sample.meta.message_id = f"<bench-{index + 1}@example.test>"
    return samples


def write_dataset(path: Path, samples: list[Sample]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["label", "from", "reply_to", "subject", "auth_results", "snippet"])
        for sample in samples:
            meta = sample.meta
            writer.writerow([
                sample.label, meta.from_, meta.reply_to, meta.subject,
                meta.auth_results, meta.snippet,
            ])


def print_metrics(results: list[tuple[Sample, str, float, str, str]]) -> bool:
    tp = sum(1 for s, pred, *_ in results if s.label == "spam" and pred == "spam")
    fp = sum(1 for s, pred, *_ in results if s.label == "ham" and pred == "spam")
    tn = sum(1 for s, pred, *_ in results if s.label == "ham" and pred == "ham")
    fn = sum(1 for s, pred, *_ in results if s.label == "spam" and pred == "ham")
    errors = sum(1 for _, pred, *_ in results if pred not in ("spam", "ham"))
    total = len(results)

    accuracy = (tp + tn) / total if total else 0.0
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0

    print("")
    print("Confusion matrix")
    print("                 predicted spam   predicted ham   errors")
    print(f"  actual spam    {tp:>14}   {fn:>13}   {errors:>6}")
    print(f"  actual ham     {fp:>14}   {tn:>13}")
    print("")
    print(f"Total:      {total}")
    print(f"Accuracy:   {accuracy:.3f}")
    print(f"Precision:  {precision:.3f}  (of messages moved, how many were spam)")
    print(f"Recall:     {recall:.3f}  (of spam, how many were caught)")
    print(f"F1:         {f1:.3f}")
    print(f"False positive rate: {fpr:.3f}  (ham wrongly moved)")

    false_positives = [s for s, pred, *_ in results if s.label == "ham" and pred == "spam"]
    false_negatives = [s for s, pred, *_ in results if s.label == "spam" and pred == "ham"]
    if false_positives:
        print("\nFalse positives (legitimate mail marked spam):")
        for sample in false_positives[:10]:
            print(f"  - {sample.meta.from_} | {sample.meta.subject}")
    if false_negatives:
        print("\nFalse negatives (spam left alone):")
        for sample in false_negatives[:10]:
            print(f"  - {sample.meta.from_} | {sample.meta.subject}")

    return accuracy >= 0.90 and recall >= 0.90 and fpr <= 0.10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark the local Ollama spam classifier on synthetic mail.",
    )
    parser.add_argument("--spam", type=int, default=500, help="number of spam emails (default: 500)")
    parser.add_argument("--ham", type=int, default=150, help="number of ham emails (default: 150)")
    parser.add_argument("--hard", action="store_true",
                        help="use adversarial hard examples (for 1000, pass --spam 500 --ham 500)")
    parser.add_argument("--limit", type=int, help="classify only the first N messages")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    parser.add_argument("--num-ctx", type=int, default=2048)
    parser.add_argument("--no-snippet", action="store_true", help="use headers only")
    parser.add_argument("--out", default="spam-benchmark", help="output file prefix")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 unless accuracy >= 0.90, recall >= 0.90 and FPR <= 0.10")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.hard:
        from hard_examples import make_hard_dataset

        samples = make_hard_dataset(args.spam, args.ham)
    else:
        samples = make_dataset(args.spam, args.ham)
    if args.limit:
        samples = samples[:args.limit]

    dataset_path = Path(f"{args.out}-emails.csv")
    results_path = Path(f"{args.out}-results.csv")
    write_dataset(dataset_path, samples)
    kind = "hard adversarial" if args.hard else "synthetic"
    print(f"Generated {len(samples)} {kind} emails ({args.spam} spam, {args.ham} ham)")
    print(f"Dataset: {dataset_path.resolve()}")

    client = OllamaClient(
        model=args.model,
        base_url=args.ollama_url,
        num_ctx=args.num_ctx,
    )
    try:
        client.check_model()
    except OllamaError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    results: list[tuple[Sample, str, float, str, str, int, float, float]] = []
    started = time.perf_counter()
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    result_handle = results_path.open("w", newline="", encoding="utf-8")
    writer = csv.writer(result_handle)
    writer.writerow([
        "truth", "predicted", "confidence", "status", "from", "subject",
        "reason", "prompt_tokens", "context_pct", "latency_ms",
    ])
    result_handle.flush()

    try:
        for index, sample in enumerate(samples, start=1):
            meta = sample.meta
            if args.no_snippet:
                meta.snippet = ""
            request_started = time.perf_counter()
            try:
                result = client.classify(meta)
                pred, confidence, reason, status = result.label, result.confidence, result.reason, "ok"
                context_pct = result.context_pct
                tokens = result.prompt_tokens
            except ContextPressure as exc:
                pred, confidence, reason, status = "error", 0.0, str(exc), "context"
                context_pct = 100.0
                tokens = 0
            except (OllamaError, ValueError) as exc:
                pred, confidence, reason, status = "error", 0.0, str(exc)[:200], "error"
                context_pct = 0.0
                tokens = 0
            latency_ms = (time.perf_counter() - request_started) * 1000
            results.append((sample, pred, confidence, reason, status, tokens, context_pct, latency_ms))

            # Write each verdict immediately so an interrupted run is still usable.
            writer.writerow([
                sample.label, pred, f"{confidence:.3f}", status,
                sample.meta.from_, sample.meta.subject, reason,
                tokens, f"{context_pct:.1f}", f"{latency_ms:.1f}",
            ])
            result_handle.flush()

            if index % 10 == 0 or index == len(samples):
                elapsed = time.perf_counter() - started
                rate = index / elapsed if elapsed else 0
                eta = (len(samples) - index) / rate if rate else 0
                correct = sum(1 for s, p, *_ in results if s.label == p)
                print(f"  [{index}/{len(samples)}] accuracy={correct/index:.3f} "
                      f"{rate:.1f}/s eta={eta/60:.1f}m", flush=True)
    finally:
        result_handle.close()

    elapsed = time.perf_counter() - started
    latencies = sorted(result[7] for result in results)
    p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0
    meta = {
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "rows": len(results),
        "spam_rows": sum(1 for sample in samples if sample.label == "spam"),
        "ham_rows": sum(1 for sample in samples if sample.label == "ham"),
        "elapsed_seconds": round(elapsed, 3),
        "emails_per_second": round(len(results) / elapsed, 3) if elapsed else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "p95_latency_ms": round(p95, 1),
        "started_at": started_at,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "num_ctx": args.num_ctx,
        "snippet": not args.no_snippet,
    }
    meta_path = Path(f"{args.out}-meta.json")
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"\nFinished in {elapsed/60:.1f} minutes "
          f"({len(samples)/elapsed:.1f} emails/s, "
          f"{sum(latencies)/len(latencies):.0f} ms/email avg, "
          f"{p95:.0f} ms p95)")
    print(f"Results: {results_path.resolve()}")
    print(f"Timing:  {meta_path.resolve()}")
    ok = print_metrics(results)
    if args.strict and not ok:
        print("\nSTRICT: benchmark did not meet the required thresholds.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
