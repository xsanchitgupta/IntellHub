"""Which code an offence is booked under, and the section that replaced it.

The Bharatiya Nyaya Sanhita, 2023 (BNS) came into force on 1 July 2024 and replaced
the Indian Penal Code, 1860 (IPC). The date of the offence decides the code: an offence
committed on or after 1 July 2024 is booked under the BNS; one committed before it stays
under the IPC (no retrospective penal law — Article 20(1)). An FIR that gets this wrong
is booked under the wrong statute, and the charge-sheet inherits the error.

What this module knows, and how it was checked
----------------------------------------------
* ``CROSSWALK`` maps every IPC section in the corpus's Section master to its BNS
  successor, down to the sub-section, with the change the table records. Each row was
  read off the correspondence table compiled by the Central Academy for Police Training,
  Bhopal (A. K. Yadav, IPS) — "Correspondence table and comparison summary of the BNS,
  2023 to the IPC, 1860", as published by Kerala Prisons — not from memory or a blog.
* BNS 304 (snatching) is new: it has no IPC predecessor, so snatching before 1 July
  2024 is booked as theft or IPC 356.
* NDPS, the IT Act and the Karnataka Forest Act were not replaced and are unaffected.

What it does not do: decide which section fits the facts. It says which code applies on
a date and what the equivalent number is; choosing the offence is the officer's call.
"""
from datetime import date, datetime

BNS_COMMENCEMENT = date(2024, 7, 1)
SOURCE = ("Correspondence table and comparison summary of the Bharatiya Nyaya Sanhita, 2023 to the "
          "Indian Penal Code, 1860 — Central Academy for Police Training, Bhopal (A. K. Yadav, IPS)")

# IPC section -> (BNS section, subject, what changed)
CROSSWALK = {
    "34": ("3(5)", "Acts done by several persons in furtherance of common intention", "No change."),
    "302": ("103(1)", "Punishment for murder", "No change; included as a sub-section."),
    "304": ("105", "Punishment for culpable homicide not amounting to murder",
            "Minimum imprisonment of five years added; fine made mandatory."),
    "323": ("115(2)", "Punishment for voluntarily causing hurt", "Fine may now extend to ten thousand rupees."),
    "356": ("134", "Assault or criminal force in attempt to commit theft of property carried by a person",
            "No change."),
    "379": ("303(2)", "Punishment for theft", "Punishment for subsequent convictions added."),
    "380": ("305(a)", "Theft in a dwelling house",
            "BNS 305 also covers means of transportation and places of worship (clauses (b)-(e), new)."),
    "384": ("308(2)", "Punishment for extortion", "Upper limit raised from three to seven years."),
    "386": ("308(5)", "Extortion by putting a person in fear of death or grievous hurt", "No change."),
    "420": ("318(4)", "Cheating and dishonestly inducing delivery of property", "No change."),
    "457": ("331(4)", "Lurking house-trespass or house-breaking by night to commit an offence",
            "'By night' is now 'after sunset and before sunrise'."),
}
# BNS sections with no IPC predecessor.
BNS_NEW = {
    "304(2)": "Snatching is a new offence in the BNS. Before 1 July 2024 it is booked as theft (IPC 379), "
              "with IPC 356 where criminal force was used.",
}
UNAFFECTED = {"NDPS", "IT Act", "Karnataka Forest Act"}


def _as_date(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("T", " ")[:19]).date()
    except ValueError:
        try:
            return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def code_for(incident_date):
    """'BNS' or 'IPC' — the general penal code in force on the date of the offence."""
    d = _as_date(incident_date)
    if d is None:
        return None
    return "BNS" if d >= BNS_COMMENCEMENT else "IPC"


def lookup(act, section):
    """The cross-walk row for one section, in either direction, or None."""
    act, section = str(act or "").strip(), str(section or "").strip()
    if act == "IPC" and section in CROSSWALK:
        bns, subject, change = CROSSWALK[section]
        return {"ipc": section, "bns": bns, "subject": subject, "change": change}
    if act == "BNS":
        for ipc, (bns, subject, change) in CROSSWALK.items():
            if bns == section:
                return {"ipc": ipc, "bns": bns, "subject": subject, "change": change}
        if section in BNS_NEW:
            return {"ipc": None, "bns": section, "subject": "Snatching", "change": BNS_NEW[section]}
    return None


def check(sections, incident_date):
    """Findings for the sections booked on one FIR, given the date of the offence.

    sections: iterable of (act, section). Returns a list of
    {code, act, section, message, suggestion: {act, section} | None}.
    """
    d = _as_date(incident_date)
    if d is None:
        return []
    in_force = "BNS" if d >= BNS_COMMENCEMENT else "IPC"
    out = []
    for act, section in sections:
        act, section = str(act).strip(), str(section).strip()
        if act in UNAFFECTED:
            continue
        if in_force == "BNS" and act == "IPC":
            row = CROSSWALK.get(section)
            out.append({
                "code": "repealed_code", "act": act, "section": section,
                "message": (f"Offence of {d.isoformat()} is booked under IPC {section}; the IPC was replaced by the "
                            f"BNS on {BNS_COMMENCEMENT.isoformat()}."
                            + (f" The equivalent is BNS {row[0]} ({row[1]})." if row else "")),
                "suggestion": {"act": "BNS", "section": row[0]} if row else None,
            })
        elif in_force == "IPC" and act == "BNS":
            back = lookup("BNS", section)
            out.append({
                "code": "bns_before_commencement", "act": act, "section": section,
                "message": (f"Offence of {d.isoformat()} is booked under BNS {section}, which came into force only on "
                            f"{BNS_COMMENCEMENT.isoformat()}."
                            + (f" The section in force then was IPC {back['ipc']}." if back and back.get("ipc")
                               else (" " + back["change"] if back else ""))),
                "suggestion": {"act": "IPC", "section": back["ipc"]} if back and back.get("ipc") else None,
            })
    return out


def table():
    """The whole cross-walk, for display."""
    rows = [{"ipc": ipc, "bns": bns, "subject": subject, "change": change}
            for ipc, (bns, subject, change) in sorted(CROSSWALK.items(), key=lambda kv: int(kv[0]))]
    rows += [{"ipc": None, "bns": bns, "subject": "Snatching", "change": note} for bns, note in BNS_NEW.items()]
    return {"commencement": BNS_COMMENCEMENT.isoformat(), "source": SOURCE, "rows": rows,
            "unaffected_acts": sorted(UNAFFECTED)}
