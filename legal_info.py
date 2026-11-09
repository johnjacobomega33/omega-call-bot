"""Legal Beagle: information-only legal help inside Pixie (branch legal-beagle-info, NOT deployed).

Rules (see /workspace/legalbeagle/PLAN.md):
- Every legal word Pixie speaks comes from this file. The LLM only routes intents; it never writes
  legal explanations.
- General information about how a process works + names of official forms + referrals. Never what
  the caller personally should do, which form he needs, or what to write (UPL; People v. Landlords
  Professional Services (1989) 215 Cal.App.3d 1599). Pure general published info written/approved by an
  attorney is outside LDA registration (Bus. & Prof. Code 6400(d)(2)).
- Every entry is attorney_review="pending" until a California attorney reviews it.
"""
from __future__ import annotations

import re

LAST_CHECKED = "2026-11-09"

# Spoken every time the caller first enters Legal Beagle in a call, and on request. <=80 words (~28 s).
CAN_CANT_TEXT = (
    "Here's what Legal Beagle can and can't do. I can explain how SVP cases and court processes "
    "generally work, tell you what official forms exist and read their instructions, help you "
    "organize your timeline and questions for your lawyer, and give you contact info for Patients' "
    "Rights, the State Bar, and the courts. I can't give legal advice about your case, tell you what "
    "to file or write, contact your lawyer or the court for you, or replace your attorney."
)
CAN_CANT_MARK = "Here's what Legal Beagle can and can't do."

REMINDER = "That's general information, not legal advice. For your own case, ask your lawyer."

REDIRECT = (
    "I can't tell you what to do in your own case. That's legal advice, and only a lawyer can give it. "
    "I can give you general information about how this works, tell you what official forms exist, or "
    "save this as a question for your lawyer."
)

CONTACT_REDIRECT = (
    "I can't contact your lawyer or the court for you. I can save your questions so they're ready "
    "when you talk to your lawyer, and I can read you contact information for Patients' Rights and "
    "the State Bar."
)

REFERRALS = (
    "Here are some contacts. Your own lawyer is the person to ask about your case. Patients' Rights "
    "Advocates at Coalinga: 1, 5 5 9, 9 3 4, 3 8 6 0. The D S H Ombudsman: 1, 8 0 0, 8 9 6, 4 0 4 2. "
    "The State Bar complaint line: 1, 8 0 0, 8 4 3, 9 0 5 3. For civil legal aid, Central California "
    "Legal Services: 1, 8 0 0, 6 7 5, 8 0 0 1."
)

PRIVACY = "You're on a shared unit phone, so general questions work best. Please don't share private details."

# Each topic: keywords for routing, general text (paraphrasing the official source; pending review),
# official form names to mention, and sources. No victim/witness information is ever stored.
TOPICS: dict[str, dict] = {
    "probable_cause": {
        "title": "the probable cause hearing",
        "keywords": ["probable cause", "6602", "6601", "pc hearing", "petition filed", "before trial"],
        "text": ("In general, after an SVP petition is filed, a judge reviews it, and the law says the "
                 "probable cause hearing should start within 10 calendar days of that order. The person "
                 "has a right to a lawyer at the hearing. If the judge finds probable cause, the person "
                 "stays in custody until trial. Continuances are allowed only for good cause."),
        "forms": [],
        "sources": ["WIC 6601.5", "WIC 6602"],
    },
    "trial_delays": {
        "title": "SVP trial delays and continuances",
        "keywords": ["delay", "continu", "speedy", "postpon", "taking so long", "waiting for trial",
                     "trial date", "years waiting", "dismiss"],
        "text": ("In general, the law says a trial continuance needs a written request with supporting "
                 "declarations and a finding of good cause on the record. California courts have said "
                 "people facing SVP petitions have a due-process right to a timely trial. Courts look at "
                 "how long the delay is, the reasons for it, whether the person asked for trial, and the "
                 "harm. Delays requested by a person's own lawyer usually count against the person, "
                 "unless there's a breakdown in the public defender system."),
        "forms": [],
        "sources": ["WIC 6603(c)", "Camacho v. Superior Court (2023)", "People v. Litmon (2008)",
                    "People v. Superior Court (Vasquez) (2018)", "In re Butler (2020)"],
    },
    "trial_rights": {
        "title": "rights at an SVP trial",
        "keywords": ["trial right", "jury", "own expert", "my expert", "right to an expert", "6603"],
        "text": ("In general, at an SVP trial the person has the right to a jury, a lawyer, and experts, "
                 "including appointed experts for someone who can't afford them, plus access to relevant "
                 "records and reports. A jury verdict must be unanimous, and the state must prove the "
                 "case beyond a reasonable doubt."),
        "forms": [],
        "sources": ["WIC 6603", "WIC 6604"],
    },
    "replacing_counsel": {
        "title": "Marsden hearings and replacing an appointed lawyer",
        "keywords": ["marsden", "new lawyer", "replace my lawyer", "different lawyer", "fire my",
                     "public defender", "lawyer won't", "attorney won't", "never calls", "call me back",
                     "can't reach my lawyer", "can't get hold"],
        "text": ("In general, a Marsden hearing is where a person with an appointed lawyer can tell the "
                 "judge why he wants a different appointed lawyer. The California Supreme Court has said "
                 "people in SVP cases are entitled to one. Also in general, when someone has a lawyer, "
                 "courts usually accept motions only from the lawyer, and a request to replace counsel "
                 "is the usual exception. Separately, the State Bar takes complaints about lawyers, and "
                 "not responding to letters or calls is one of its listed examples."),
        "forms": [],
        "sources": ["People v. Carter (2024)", "People v. McArthur (1992)", "State Bar complaint pamphlet"],
    },
    "annual_exam": {
        "title": "the annual examination",
        "keywords": ["annual", "yearly", "6604.9", "annual report", "evaluation", "evaluator"],
        "text": ("In general, a committed person must be examined at least once a year. The person may "
                 "hire his own expert, or ask the court to appoint one if he can't afford it. The report "
                 "is filed with the committing court and served on the person."),
        "forms": [],
        "sources": ["WIC 6604.9"],
    },
    "conditional_release": {
        "title": "conditional release under section 6608",
        "keywords": ["conditional release", "6608", "conrep", "get out", "release", "liberty healthcare",
                     "placement", "housing"],
        "text": ("In general, a committed person can petition for conditional release, with or without "
                 "the hospital's recommendation. The court first screens the petition. No hearing can be "
                 "held until the person has been committed at least one year, and after a denial there's "
                 "generally a one-year wait. If release is granted, the person goes to the CONREP program, "
                 "run by Liberty Healthcare, which looks for housing and gives public notice. State "
                 "reports say placement can take a year or more."),
        "forms": [],
        "sources": ["WIC 6608", "WIC 6608.5", "DSH SVP CONREP fact sheet", "State Auditor 2023-130"],
    },
    "discharge": {
        "title": "unconditional discharge under section 6605",
        "keywords": ["unconditional", "discharge", "6605", "show cause"],
        "text": ("In general, if the court finds probable cause at a show-cause hearing, the person gets a "
                 "trial on whether he still meets the SVP definition. He can have a jury, and the state "
                 "has to prove its case beyond a reasonable doubt. After a loss there's generally a "
                 "one-year wait before the next petition."),
        "forms": [],
        "sources": ["WIC 6605"],
    },
    "habeas": {
        "title": "habeas corpus petitions",
        "keywords": ["habeas", "hc-001", "hc 001", "mc-275", "mc 275", "writ"],
        "text": ("In general, a habeas corpus petition asks a court to review whether someone is being "
                 "held lawfully, or the conditions he's held in. Since July 1, 2026, superior courts "
                 "require the Judicial Council form HC-001, which replaced the older MC-275. Challenges "
                 "to a commitment generally go to the court that ordered it. Challenges to conditions "
                 "generally go to the county where the person is held. The court generally has to rule "
                 "within 60 days."),
        "forms": ["HC-001, Petition for Writ of Habeas Corpus"],
        "sources": ["Cal. Rules of Court 4.551", "HC-001 instructions"],
    },
    "fee_waiver": {
        "title": "court fee waivers",
        "keywords": ["fee waiver", "filing fee", "can't afford", "fw-001", "fw 001", "court fees"],
        "text": ("In general, a court can waive filing fees for people on certain public benefits, people "
                 "whose income is at or below 200 percent of the federal poverty level, or people who "
                 "can't pay basic needs and fees. The request is confidential. A waiver generally expires "
                 "60 days after the case ends."),
        "forms": ["FW-001, Request to Waive Court Fees", "FW-003, the court's order"],
        "sources": ["Gov. Code 68632", "CA Courts Self-Help Guide: fee waivers", "FW-001-INFO"],
    },
    "patients_rights": {
        "title": "patients' rights and the Patients' Rights Advocates",
        "keywords": ["patient rights", "patients' rights", "patients rights", "advocate", "grievance",
                     "complaint about staff", "ombudsman", "denied my", "rights denied"],
        "text": ("In general, state hospital patients have rights that can't be taken away, including "
                 "complaint procedures, access to a patients' rights advocate, and confidential contact "
                 "with an attorney. Other rights can be denied only for good cause, and the denial has to "
                 "be documented. The Patients' Rights Advocates at Coalinga are run by Disability Rights "
                 "California, phone 1, 5 5 9, 9 3 4, 3 8 6 0. The D S H Ombudsman is 1, 8 0 0, 8 9 6, "
                 "4 0 4 2."),
        "forms": [],
        "sources": ["9 CCR 883", "9 CCR 884", "DSH Patients' Rights page", "DRC directory"],
    },
    "mail_rules": {
        "title": "mail rules at Coalinga",
        "keywords": ["mail", "letter", "envelope", "legal mail", "confidential mail", "postage"],
        "text": ("In general, mail at Coalinga is opened and inspected for contraband. Mail to or from "
                 "attorneys, courts, and government officials counts as confidential mail and isn't "
                 "read. Other mail, including mail from services like this one, isn't confidential."),
        "forms": [],
        "sources": ["Seeboth v. DSH", "9 CCR 881(c)", "DSH-Coalinga family and friend resources"],
    },
    "state_bar": {
        "title": "complaints about a lawyer to the State Bar",
        "keywords": ["state bar", "complain about my lawyer", "complaint against", "bar complaint",
                     "misconduct", "report my lawyer"],
        "text": ("In general, the State Bar of California takes complaints about lawyers on its Attorney "
                 "Misconduct Complaint form. The form can be mailed to the Office of Chief Trial Counsel "
                 "Intake, 845 South Figueroa Street, Los Angeles, California 9 0 0 1 7. The hotline is "
                 "1, 8 0 0, 8 4 3, 9 0 5 3. The State Bar lists a lawyer not responding to letters or "
                 "calls as one example of a problem."),
        "forms": ["the State Bar's Attorney Misconduct Complaint form"],
        "sources": ["State Bar complaint form", "State Bar pamphlet"],
    },
    "find_lawyer": {
        "title": "finding a lawyer",
        "keywords": ["find a lawyer", "hire a lawyer", "lawyer referral", "need a lawyer", "private attorney"],
        "text": ("In general, the State Bar certifies lawyer referral services, and the California Courts "
                 "self-help website has a page on hiring a lawyer. In SVP cases the court appoints a "
                 "lawyer for people who can't afford one."),
        "forms": [],
        "sources": ["State Bar lawyer referral", "CA Courts: Hire a lawyer", "WIC 6603"],
    },
}

for _t in TOPICS.values():
    _t.setdefault("attorney_review", "pending")
    _t.setdefault("last_checked", LAST_CHECKED)

ADVICE_RE = re.compile(
    r"\bshould i\b|\bwhat (?:should|do) i (?:do|file|write|say)\b|\bdo i have a (?:case|claim)\b|"
    r"\bwhich form (?:do|should) i\b|\bwhat form do i need\b|\bwhat do i (?:write|put)\b|"
    r"\bwill i win\b|\bcan i win\b|\bam i going to (?:win|get out)\b|\bmy chances\b|"
    r"\bis my lawyer (?:doing|wrong)\b|\bwrite (?:it|this|a letter|the letter|my motion) for me\b|"
    r"\bfill (?:it|this|the form|out) ?(?:out )?for me\b|\bfile (?:it|this) for me\b|\bhelp me (?:fill|write|file)\b",
    re.I)
CONTACT_RE = re.compile(r"\b(?:call|contact|email|write to|tell|reach) (?:my lawyer|my attorney|"
                        r"my public defender|the court|the judge|the da)\b(?: for me)?", re.I)
LEGAL_CAPS_RE = re.compile(r"what (?:can|does) legal beagle do|what can you do (?:with|for) legal|"
                           r"legal beagle (?:help|menu)|what legal help", re.I)
REFERRAL_RE = re.compile(r"\b(?:referral|phone number|contact info|who (?:can|do) i call|numbers?)\b", re.I)
LEGAL_HINT_RE = re.compile(
    r"\blegal\b|\blegal beagle\b|\blawyer\b|\battorney\b|\bpublic defender\b|\bcourt\b|\bjudge\b|"
    r"\btrial\b|\bhearing\b|\bpetition\b|\bhabeas\b|\bsvp\b|\b660[0-9]|\bconrep\b|\bmarsden\b|"
    r"\bstate bar\b|\bfee waiver\b|\bcommitment\b|\bdischarge\b|\bconditional release\b|\bmotion\b",
    re.I)


def looks_legal(text: str) -> bool:
    return bool(LEGAL_HINT_RE.search(text or "") or LEGAL_CAPS_RE.search(text or ""))


def is_advice_request(text: str) -> bool:
    return bool(ADVICE_RE.search(text or ""))


def match_topic(text: str) -> str | None:
    low = (text or "").lower()
    best, score = None, 0
    for key, t in TOPICS.items():
        s = sum(len(k) for k in t["keywords"] if k in low)
        if s > score:
            best, score = key, s
    return best


def topic_menu() -> str:
    return ("I have general information on: the probable cause hearing, trial delays, trial rights, "
            "replacing an appointed lawyer, the annual exam, conditional release, discharge, habeas "
            "corpus, fee waivers, patients' rights, mail rules, State Bar complaints, and finding a "
            "lawyer. Which one?")


def topic_answer(key: str) -> str:
    t = TOPICS[key]
    out = f"Here's general information about {t['title']}. {t['text']}"
    if t["forms"]:
        out += " The official form" + ("s" if len(t["forms"]) > 1 else "") + " for this topic: " + \
               "; ".join(t["forms"]) + ". I can read the court's instructions if you'd like."
    return f"{out} {REMINDER}"


def respond(text: str, *, intro_needed: bool) -> str:
    """Deterministic Legal Beagle reply. intro_needed=True on the first Legal Beagle turn of a call."""
    if LEGAL_CAPS_RE.search(text or ""):
        return f"{CAN_CANT_TEXT} {PRIVACY}"
    parts: list[str] = [CAN_CANT_TEXT] if intro_needed else []
    key = match_topic(text)
    if CONTACT_RE.search(text or ""):
        parts.append(CONTACT_REDIRECT)
    elif is_advice_request(text):
        parts.append(REDIRECT)
        if key:
            parts.append(topic_answer(key))
        parts.append("Want me to save that as a question for your lawyer?")
    elif key:
        parts.append(topic_answer(key))
    elif REFERRAL_RE.search(text or ""):
        parts.append(REFERRALS)
    else:
        parts.append(topic_menu())
    return " ".join(parts)


_SAVE_PREFIX_RE = re.compile(
    r"^(?:please\s+)?(?:save|add|note|put down|write down)\s+(?:a\s+|this\s+|that\s+)?(?:question\s+)?"
    r"(?:for|to ask)\s+my\s+(?:lawyer|attorney|public defender)\s*[:,-]?\s*", re.I)


def question_for_attorney_title(question: str) -> str:
    q = (question or "").strip()
    q = _SAVE_PREFIX_RE.sub("", q).strip() or q
    return ("For my lawyer: " + q)[:200]
