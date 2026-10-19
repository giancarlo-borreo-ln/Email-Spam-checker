"""Hard, adversarial spam/ham examples.

These deliberately mix the signals that make real inboxes difficult:

* spam that passes SPF/DKIM/DMARC because it comes from the spammer's own domain
* spam with no links and a personal tone (compromised-account / BEC fraud)
* legitimate mail with spammy words ("urgent", "free", "50% off", money)
* legitimate mail that fails authentication because it was forwarded
* lookalike domains with perfect display names
* short invoice/document/voicemail lures with attachments

Label policy: unsolicited commercial or fraudulent mail is spam. Opted-in
newsletters, transactional mail, security notices, and personal mail are ham.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from llm_spam_scan import EmailMeta

FIRST_NAMES = ["Sarah", "Michael", "Laura", "Daniel", "Priya", "Anna", "Luca", "Emma", "Omar", "Grace"]
LAST_NAMES = ["Lee", "Chen", "Bianchi", "Rossi", "Khan", "Weber", "Silva", "Novak", "Haddad", "Kim"]
AMOUNTS = ["$4,820", "$1,284", "$399.99", "$2,140", "€2,950", "£740", "$64.20"]
STORES = ["Decathlon", "IKEA", "Amazon", "Zara", "Blue Bottle", "Booking.com"]


@dataclass
class HardSample:
    label: str
    meta: EmailMeta


def _name(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _handle(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES).lower()}.{rng.choice(LAST_NAMES).lower()}"


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
# Hard spam
# --------------------------------------------------------------------------- #

def spam_compromised_colleague(rng, i):
    domain = f"{rng.choice(['acme-corp', 'northwind', 'contoso'])}.example"
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        reply_to=f"{_handle(rng)}@gmail.example",
        subject=rng.choice(["Quick favor", "Are you around?", "Need a hand today"]),
        date="Mon, 19 Oct 2026 09:12:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Hi, I'm stuck in back-to-back meetings and need a small favor. Could you buy four $200 gift cards for a client and send me the codes? I'll reimburse you today. Please don't call, just reply.",
    )


def spam_ceo_lookalike(rng, i):
    real = "acme-corp.example"
    domain = "acme-corp-exec.example"
    name = _name(rng)
    return EmailMeta(
        from_=f"{name}, CEO <ceo@{domain}>",
        reply_to=f"{_handle(rng)}@{domain}",
        subject="Confidential request - handle today",
        date="Mon, 19 Oct 2026 08:55:00 +0000",
        auth_results=_auth(True, domain),
        snippet="I need this handled discreetly before the board meeting. Purchase four $500 gift cards for a client and send me the codes. Keep this between us and do not call my office line.",
    )


def spam_invoice_pdf(rng, i):
    domain = "vendor-payments.example"
    amount = rng.choice(AMOUNTS)
    return EmailMeta(
        from_=f"Accounts Receivable <billing@{domain}>",
        subject=f"Invoice {rng.randint(1000,9999)} attached - {amount} due today",
        date="Mon, 19 Oct 2026 07:40:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Please find the attached invoice for {amount}. Payment is due today. Open the PDF to view the updated remittance and bank details.",
    )


def spam_docusign(rng, i):
    domain = "docusign-secure.example"
    return EmailMeta(
        from_=f"DocuSign <no-reply@{domain}>",
        subject=rng.choice([
            "Please sign: Employment agreement",
            "Completed: document requires your signature",
            "Action required: sign before 5pm",
        ]),
        date="Mon, 19 Oct 2026 10:02:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Review and sign the document at https://example.invalid/sign within 24 hours. This link is unique to you and will expire after use.",
    )


def spam_voicemail(rng, i):
    domain = "voicemail-center.example"
    number = f"+1 (415) 555-01{rng.randint(10, 99)}"
    return EmailMeta(
        from_=f"Voicemail <notify@{domain}>",
        subject=f"New voicemail from {number}",
        date="Mon, 19 Oct 2026 11:14:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"You have a new {rng.randint(20, 90)}-second voicemail from {number}. Listen at https://example.invalid/vm. This message will expire in 24 hours.",
    )


def spam_thread_hijack(rng, i):
    domain = "consulting-partners.example"
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        reply_to=f"accounts@{domain}",
        subject="Re: Project update and outstanding invoice",
        date="Mon, 19 Oct 2026 09:33:00 +0000",
        auth_results=_auth(True, domain),
        snippet="As discussed yesterday, please find the revised invoice attached. I also updated our bank details, so use the new account for this payment. Thanks - sent from my phone.",
    )


def spam_bank_lookalike(rng, i):
    bank = rng.choice(["Chase", "Citi", "HSBC", "Wells Fargo"])
    domain = f"{bank.lower().replace(' ', '-')}-secure-login.example"
    return EmailMeta(
        from_=f"{bank} Online Banking <alerts@{domain}>",
        reply_to=f"security@{domain}",
        subject=rng.choice([
            "Your account has been limited",
            "Unusual activity - verify within 24 hours",
            "We blocked a suspicious payment",
        ]),
        date="Mon, 19 Oct 2026 06:48:00 +0000",
        auth_results=_auth(True, domain),
        snippet="We noticed unusual activity on your account. Verify your identity at https://example.invalid/verify to restore access within 24 hours or your account will remain limited.",
    )


def spam_hr_payroll(rng, i):
    domain = "company-benefits.example"
    return EmailMeta(
        from_=f"People Operations <hr@{domain}>",
        subject="Updated payroll information required",
        date="Mon, 19 Oct 2026 08:05:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Our records show your direct deposit details are outdated. Update them at https://example.invalid/payroll before the next pay cycle to avoid a delay.",
    )


def spam_job_offer(rng, i):
    domain = "remote-careers.example"
    return EmailMeta(
        from_=f"Talent Team <jobs@{domain}>",
        subject=f"Remote assistant - ${rng.choice([35, 45, 55])}/hour, no experience",
        date="Mon, 19 Oct 2026 12:20:00 +0000",
        auth_results=_auth(True, domain),
        snippet="We found your profile and would like to offer you a flexible remote role. Reply with your phone number and availability to start this week. Weekly pay, no interview required.",
    )


def spam_personal_short(rng, i):
    domain = rng.choice(["outlook.example", "gmail.example", "proton.example"])
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject=rng.choice(["Hey", "Are you still interested?", "Quick question"]),
        date="Mon, 19 Oct 2026 13:10:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Hey, are you still interested? I have a proposition that could be worth your time. Reply YES and I'll send the details.",
    )


def spam_fake_newsletter(rng, i):
    domain = "wellness-daily.example"
    return EmailMeta(
        from_=f"Health Insider <news@{domain}>",
        subject=rng.choice([
            "Doctors don't want you to know this",
            "The 5-minute trick for joint pain",
            "This supplement is flying off shelves",
        ]),
        date="Mon, 19 Oct 2026 07:15:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<mailto:unsubscribe@{domain}>",
        snippet="This week: the one supplement doctors allegedly avoid, a 5-minute trick for joint pain, and a 'free' report. Unsubscribe at the bottom of this email.",
    )


def spam_subscription_renewal(rng, i):
    domain = "norton-renewals.example"
    amount = rng.choice(AMOUNTS)
    return EmailMeta(
        from_=f"Norton Billing <billing@{domain}>",
        subject=f"Your subscription renewed - {amount}",
        date="Mon, 19 Oct 2026 05:25:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Your annual protection plan was renewed for {amount}. If you did not authorize this charge, call 1-800-555-0199 immediately for a refund.",
    )


def spam_tax_refund(rng, i):
    domain = "tax-service.example"
    amount = rng.choice(AMOUNTS)
    return EmailMeta(
        from_=f"Tax Refund Office <refunds@{domain}>",
        subject=f"Final notice: refund of {amount} pending",
        date="Mon, 19 Oct 2026 04:40:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Complete the verification form with your Social Security number and bank account to release your refund. Failure to respond within 24 hours will cancel the payment.",
    )


def spam_malware_short(rng, i):
    domain = "document-share.example"
    return EmailMeta(
        from_=f"Scan Center <scan@{domain}>",
        subject=rng.choice(["Scanned document", "Your document is ready", "Fax received"]),
        date="Mon, 19 Oct 2026 03:30:00 +0000",
        auth_results=_auth(True, domain),
        snippet="The document you requested is attached as a ZIP file. Password: 1234. Open it on a desktop computer to view the scan.",
    )


def spam_calendar_invite(rng, i):
    domain = "meeting-scheduler.example"
    return EmailMeta(
        from_=f"Calendar <invite@{domain}>",
        subject=f"Invitation: Budget review @ Wed {rng.randint(9, 16)}:00",
        date="Mon, 19 Oct 2026 09:00:00 +0000",
        auth_results=_auth(True, domain),
        snippet="You have been invited to a budget review. Join the meeting at https://example.invalid/meet. Please confirm attendance and review the attached agenda.",
    )


def spam_reply_chain(rng, i):
    domain = "supplier-invoices.example"
    return EmailMeta(
        from_=f"Accounts <accounts@{domain}>",
        subject=f"Re: Re: Invoice INV-{rng.randint(1000,9999)}",
        date="Mon, 19 Oct 2026 11:45:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Following up on the thread below. Our payment details have changed; please use the attached remittance information for the outstanding balance. Sorry for the extra step.",
    )


def spam_qr(rng, i):
    domain = "secure-portal.example"
    return EmailMeta(
        from_=f"Account Verification <verify@{domain}>",
        subject="Verify your account to avoid suspension",
        date="Mon, 19 Oct 2026 08:35:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Scan the QR code in the attached image to verify your identity and avoid suspension. Do not share this code with anyone.",
    )


def spam_charity(rng, i):
    domain = "hope-relief.example"
    return EmailMeta(
        from_=f"Hope Foundation <donate@{domain}>",
        subject=rng.choice(["Help us reach our goal tonight", "Your gift doubles today", "Emergency appeal"]),
        date="Mon, 19 Oct 2026 06:10:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Your gift can provide food and medicine to families in crisis. Donate now at https://example.invalid/donate before midnight and your donation will be matched.",
    )


def spam_crypto_withdrawal(rng, i):
    domain = "coinvault-support.example"
    return EmailMeta(
        from_=f"CoinVault <support@{domain}>",
        subject="Withdrawal pending - confirm now",
        date="Mon, 19 Oct 2026 02:05:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"A withdrawal of {rng.choice(['0.84 BTC','12.4 ETH','5,200 USDT'])} is pending. Confirm the transaction at https://example.invalid/confirm within 30 minutes or the funds will be returned.",
    )


def spam_legal_threat(rng, i):
    domain = "law-offices.example"
    amount = rng.choice(AMOUNTS)
    return EmailMeta(
        from_=f"Legal Department <notice@{domain}>",
        subject="Final notice before legal action",
        date="Mon, 19 Oct 2026 10:55:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Our client has instructed us to proceed unless the outstanding balance of {amount} is paid today. Contact us immediately to avoid court proceedings.",
    )


SPAM_TEMPLATES = [
    spam_compromised_colleague, spam_ceo_lookalike, spam_invoice_pdf,
    spam_docusign, spam_voicemail, spam_thread_hijack, spam_bank_lookalike,
    spam_hr_payroll, spam_job_offer, spam_personal_short, spam_fake_newsletter,
    spam_subscription_renewal, spam_tax_refund, spam_malware_short,
    spam_calendar_invite, spam_reply_chain, spam_qr, spam_charity,
    spam_crypto_withdrawal, spam_legal_threat,
]


# --------------------------------------------------------------------------- #
# Hard ham
# --------------------------------------------------------------------------- #

def ham_retailer_urgent(rng, i):
    store = rng.choice(STORES)
    domain = f"{store.lower().replace(' ', '').split('.')[0]}.example"
    return EmailMeta(
        from_=f"{store} <deals@{domain}>",
        subject=rng.choice(["URGENT: your 50% off code expires tonight", "Last chance: 30% off ends tonight"]),
        date="Mon, 19 Oct 2026 09:00:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet=f"Your member-only discount ends at midnight. Use code SAVE50 at checkout. You're receiving this because you subscribed to {store} offers.",
    )


def ham_bank_security(rng, i):
    bank = rng.choice(["Chase", "Citi", "HSBC"])
    domain = f"{bank.lower()}.com"
    return EmailMeta(
        from_=f"{bank} <alerts@{domain}>",
        subject=rng.choice(["New sign-in to your account", "Your statement is ready", "Payment scheduled"]),
        date="Mon, 19 Oct 2026 06:45:00 +0000",
        auth_results=_auth(True, domain),
        snippet="We noticed a new sign-in from a device you have used before. If this was you, no action is needed. Review your active sessions in the official app or website.",
    )


def ham_invoice_legit(rng, i):
    domain = "stripe.com"
    return EmailMeta(
        from_="Stripe <invoices@stripe.com>",
        subject=f"Invoice from Acme for {rng.choice(['October','September'])}",
        date="Mon, 19 Oct 2026 08:10:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Your invoice is attached. This is a receipt for your records; payment will be collected automatically. View it in your dashboard.",
    )


def ham_password_reset(rng, i):
    domain = "accounts.google.com"
    return EmailMeta(
        from_="Google <no-reply@accounts.google.com>",
        subject=rng.choice(["Your password was changed", "Security alert", "New sign-in on Chrome"]),
        date="Mon, 19 Oct 2026 12:00:00 +0000",
        auth_results=_auth(True, domain),
        snippet="The password for your Google Account was changed. If this wasn't you, secure your account at myaccount.google.com/security.",
    )


def ham_newsletter_clickbait(rng, i):
    domain = "thehustle.example"
    return EmailMeta(
        from_=f"The Hustle <hello@{domain}>",
        subject=rng.choice([
            "You won't believe what this CEO did",
            "The 10-minute habit that changed my week",
            "This tool is everywhere - here's why",
        ]),
        date="Mon, 19 Oct 2026 07:00:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="In today's edition: the CEO who fired his entire board, a 10-minute productivity hack, and the tool everyone is talking about.",
    )


def ham_forwarded(rng, i):
    domain = "family.example"
    name = rng.choice(["Mom", "Dad", "Aunt Linda"])
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject="Fwd: Fw: cute photos",
        date="Mon, 19 Oct 2026 18:30:00 +0000",
        auth_results=_auth(False, domain),
        snippet="Forwarding these photos from Aunt Linda. She says hi and asks when you're visiting. No need to reply all.",
    )


def ham_mailing_list(rng, i):
    domain = "pythonweekly.example"
    return EmailMeta(
        from_=f"Python Weekly <news@{domain}>",
        subject=f"Python Weekly #{rng.randint(600, 800)}",
        date="Mon, 19 Oct 2026 06:00:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="This week: PEP updates, a new async library, and 12 open-source projects. Manage your subscription or unsubscribe at the bottom.",
    )


def ham_personal_money(rng, i):
    domain = "friends.example"
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject=f"I owe you ${rng.choice([20, 40, 50])}",
        date="Mon, 19 Oct 2026 20:15:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Hey, I still owe you from dinner. I'll transfer it tonight. Let me know if you want me to send it to the same account.",
    )


def ham_travel(rng, i):
    domain = "booking.com"
    city = rng.choice(["Milan", "Berlin", "Lisbon", "Osaka"])
    return EmailMeta(
        from_="Booking.com <confirmations@booking.com>",
        subject=f"Your booking in {city} is confirmed",
        date="Mon, 19 Oct 2026 13:15:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Booking reference {rng.randint(1000,9999)}. Your hotel in {city} is confirmed. Check-in opens at 15:00. Manage your booking online.",
    )


def ham_github_security(rng, i):
    domain = "github.com"
    repo = rng.choice(["acme/api", "acme/web", "acme/infra"])
    return EmailMeta(
        from_="GitHub <notifications@github.com>",
        subject="Security alert: dependency vulnerability",
        date="Mon, 19 Oct 2026 11:30:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://github.com/notifications/unsubscribe/{rng.randint(1,999)}>",
        snippet=f"A dependency in {repo} has a known vulnerability. Review the Dependabot alert and update to the patched version.",
    )


def ham_school_urgent(rng, i):
    domain = "oakschool.example"
    return EmailMeta(
        from_="Oak School <office@oakschool.example>",
        subject="Urgent: permission form due tomorrow",
        date="Mon, 19 Oct 2026 08:20:00 +0000",
        auth_results=_auth(True, domain),
        snippet="The field trip permission form is due tomorrow. Please sign and return it via the parent portal. Contact the office if you need help.",
    )


def ham_doctor(rng, i):
    domain = "cityclinic.example"
    return EmailMeta(
        from_="City Clinic <appointments@cityclinic.example>",
        subject="Appointment reminder",
        date="Mon, 19 Oct 2026 09:00:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"This is a reminder for your appointment on {rng.choice(['Thursday','Tuesday'])} at 09:30. Reply to reschedule. Bring your insurance card.",
    )


def ham_receipt_unknown(rng, i):
    domain = "lucasbistro.example"
    return EmailMeta(
        from_="Luca's Bistro <receipts@lucasbistro.example>",
        subject="Your receipt from Luca's Bistro",
        date="Mon, 19 Oct 2026 22:10:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Thank you for dining with us. Your receipt for ${rng.randint(30, 90)}.{rng.randint(10,99)} is attached. We hope to see you again soon.",
    )


def ham_charity(rng, i):
    domain = "doctorswithoutborders.example"
    return EmailMeta(
        from_="Doctors Without Borders <news@doctorswithoutborders.example>",
        subject="Your monthly donation made a difference",
        date="Mon, 19 Oct 2026 10:00:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="Thanks to your support, we delivered 12,000 medical kits last month. Read the field update and see where your donation went.",
    )


def ham_office_pool(rng, i):
    domain = "work.example"
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject="You won the office pool",
        date="Mon, 19 Oct 2026 17:40:00 +0000",
        auth_results=_auth(True, domain),
        snippet=f"Congrats! You won the office football pool. I'll bring the ${rng.choice([20, 40, 50])} to the office tomorrow. Let me know if you want it transferred instead.",
    )


def ham_crypto_newsletter(rng, i):
    domain = "coindesk.example"
    return EmailMeta(
        from_="CoinDesk <news@coindesk.example>",
        subject=rng.choice(["Bitcoin's wild week and what's next", "Ethereum upgrade explained", "Stablecoin regulation deep dive"]),
        date="Mon, 19 Oct 2026 07:30:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="Bitcoin volatility, a new ETF filing, and a deep dive on stablecoin regulation. Read the full newsletter online.",
    )


def ham_pharmacy(rng, i):
    domain = "walgreens.example"
    return EmailMeta(
        from_="Walgreens <prescriptions@walgreens.example>",
        subject="Prescription ready for pickup",
        date="Mon, 19 Oct 2026 14:20:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Your prescription refill is ready for pickup at 123 Main St. Bring your ID. This is an automated notification.",
    )


def ham_free_coffee(rng, i):
    domain = "bluebottle.example"
    return EmailMeta(
        from_="Blue Bottle <loyalty@bluebottle.example>",
        subject="Free coffee this Friday",
        date="Mon, 19 Oct 2026 08:00:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="As a loyalty member, enjoy a free coffee this Friday. Show this email at the counter. You're receiving this because you joined our rewards program.",
    )


def ham_legal(rng, i):
    domain = "studio-legale.example"
    name = _name(rng)
    return EmailMeta(
        from_=f"{name} <{_handle(rng)}@{domain}>",
        subject="Document for review",
        date="Mon, 19 Oct 2026 16:05:00 +0000",
        auth_results=_auth(True, domain),
        snippet="Please find the signed contract attached. Let me know if the dates look right before we file it. Thanks.",
    )


def ham_sale_alert(rng, i):
    store = rng.choice(STORES)
    domain = f"{store.lower().replace(' ', '').split('.')[0]}.example"
    return EmailMeta(
        from_=f"{store} <offers@{domain}>",
        subject="Last chance: 30% off ends tonight",
        date="Mon, 19 Oct 2026 09:45:00 +0000",
        auth_results=_auth(True, domain),
        list_unsubscribe=f"<https://example.invalid/unsubscribe/{rng.randint(1,999)}>",
        snippet="Your family member offer ends tonight. Save 30% on selected furniture and home accessories. You are subscribed to our offers.",
    )


HAM_TEMPLATES = [
    ham_retailer_urgent, ham_bank_security, ham_invoice_legit,
    ham_password_reset, ham_newsletter_clickbait, ham_forwarded,
    ham_mailing_list, ham_personal_money, ham_travel, ham_github_security,
    ham_school_urgent, ham_doctor, ham_receipt_unknown, ham_charity,
    ham_office_pool, ham_crypto_newsletter, ham_pharmacy, ham_free_coffee,
    ham_legal, ham_sale_alert,
]


def make_hard_dataset(
    spam_count: int = 500,
    ham_count: int = 500,
    seed: int = 123,
) -> list[HardSample]:
    rng = random.Random(seed)
    samples: list[HardSample] = []
    for index in range(spam_count):
        template = SPAM_TEMPLATES[index % len(SPAM_TEMPLATES)]
        samples.append(HardSample("spam", template(rng, index)))
    for index in range(ham_count):
        template = HAM_TEMPLATES[index % len(HAM_TEMPLATES)]
        samples.append(HardSample("ham", template(rng, index)))
    rng.shuffle(samples)
    for index, sample in enumerate(samples):
        sample.meta.uid = str(index + 1)
        sample.meta.message_id = f"<hard-{index + 1}@example.test>"
    return samples
