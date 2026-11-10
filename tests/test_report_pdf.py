"""The Report as a PDF and the invoice summary: what an invoice gets from a range's entries (report.invoice), and how
the page saves a PDF in Pulsar's window, in an older window and in a browser. Made-up data only.

    python3 -m unittest discover -s tests
"""

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from isolation import SCRATCH as _SCRATCH  # noqa: E402,F401  first: nothing real is reachable (isolation.py)

from pulsar import report  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")


def read(*path):
    with open(os.path.join(ROOT, *path), encoding="utf-8") as fh:
        return fh.read()


def entry(project, task, minutes, billable, task_id="", service="Design", date="2026-11-03", budget="Budget A"):
    return {"date": date, "start": None, "minutes": minutes, "approved": False, "project": project, "budget": budget,
            "service": service, "serviceId": "s-" + service, "task": task, "taskId": task_id, "billable": billable,
            "billing": "", "note": "a private note"}


class InvoiceTest(unittest.TestCase):
    def test_every_task_by_project_with_a_subtotal_each_and_the_totals(self):
        inv = report.invoice([
            entry("Beta site", "Homepage", 90, True, "1"),
            entry("Beta site", "Homepage", 30, True, "1", date="2026-11-04"),
            entry("Beta site", "Status call", 30, False, "2", service="Management"),
            entry("Alpha app", "API limits", 60, True, "3"),
        ])
        self.assertEqual([p["project"] for p in inv["projects"]], ["Alpha app", "Beta site"])  # A to Z
        beta = inv["projects"][1]
        self.assertEqual([(t["task"], t["minutes"], t["billable"], t["nonBillable"]) for t in beta["tasks"]],
                         [("Homepage", 120, 120, 0), ("Status call", 30, 0, 30)])  # most time first, days summed
        self.assertEqual((beta["minutes"], beta["billable"], beta["billablePct"]), (150, 120, 80))
        self.assertEqual((inv["minutes"], inv["billable"], inv["nonBillable"], inv["unknown"], inv["billablePct"]),
                         (210, 180, 30, 0, 86))

    def test_unknown_billing_stays_unknown_and_the_share_is_of_the_known_time(self):
        """The Billable tile's rule: billing Productive does not show counts as neither, and the share is of the time
        whose billing is known. Never guessed either way."""
        inv = report.invoice([entry("P", "Known", 60, True, "1"), entry("P", "Hidden", 40, None, "2", service="Hidden")])
        hidden = next(t for t in inv["projects"][0]["tasks"] if t["task"] == "Hidden")
        self.assertEqual((hidden["billable"], hidden["nonBillable"], hidden["unknown"]), (0, 0, 40))
        self.assertEqual((inv["minutes"], inv["billable"], inv["unknown"], inv["billablePct"]), (100, 60, 40, 100))
        only = report.invoice([entry("P", "Hidden", 40, None, "2")])
        self.assertIsNone(only["billablePct"])  # nothing known: no percentage, not 0%
        self.assertIsNone(only["projects"][0]["billablePct"])

    def test_a_task_logged_on_a_billable_and_a_non_billable_service_shows_both(self):
        inv = report.invoice([entry("P", "Mixed", 45, True, "7", service="Dev"),
                              entry("P", "Mixed", 15, False, "7", service="Internal"),
                              entry("P", "Mixed", 10, None, "7", service="Hidden")])
        (t,) = inv["projects"][0]["tasks"]
        self.assertEqual((t["minutes"], t["billable"], t["nonBillable"], t["unknown"]), (70, 45, 15, 10))
        self.assertEqual(inv["billablePct"], 75)

    def test_time_on_a_service_with_no_task_is_a_line_named_after_the_service(self):
        inv = report.invoice([entry("P", "", 30, True, service="Support"), entry("P", "", 20, True, service="Support"),
                              entry("P", "", 10, False, service="Meetings"), entry("P", "", 5, True, "9")])
        lines = {t["task"]: t for t in inv["projects"][0]["tasks"]}
        self.assertEqual((lines["Support"]["minutes"], lines["Support"]["noTask"]), (50, True))
        self.assertEqual(lines["Meetings"]["minutes"], 10)
        self.assertIn("(untitled task)", lines)  # a task whose title is not known is never named after its service
        self.assertFalse(lines["(untitled task)"]["noTask"])

    def test_no_project_comes_last(self):
        inv = report.invoice([entry("", "A", 10, True, "1"), entry("zeta", "B", 10, True, "2"), entry("Alpha", "C", 10, True, "3")])
        self.assertEqual([p["project"] for p in inv["projects"]], ["Alpha", "zeta", "(no project)"])

    def test_nothing_but_what_an_invoice_needs(self):
        inv = report.invoice([entry("P", "T", 30, True, "1")])
        self.assertEqual(set(inv), {"projects", "minutes", "billable", "nonBillable", "unknown", "billablePct"})
        self.assertEqual(set(inv["projects"][0]), {"project", "tasks", "minutes", "billable", "nonBillable", "unknown",
                                                   "billablePct"})
        self.assertEqual(set(inv["projects"][0]["tasks"][0]),
                         {"taskId", "task", "noTask", "minutes", "billable", "nonBillable", "unknown"})
        self.assertNotIn("private note", json.dumps(inv))

    def test_the_percentage_rounds_half_up_as_the_pages_pct_does(self):
        # Math.round(12.5) is 13 where Python's round(12.5) is 12: the invoice and the Billable tile must agree
        self.assertEqual(report.percent(1, 8), 13)
        self.assertEqual(report.percent(3, 8), 38)
        self.assertEqual((report.percent(1, 3), report.percent(2, 3), report.percent(5, 5)), (33, 67, 100))
        self.assertIsNone(report.percent(0, 0))

    def test_the_report_carries_it_for_its_range_and_its_totals_match_the_days(self):
        t0 = datetime(2026, 11, 2, tzinfo=timezone.utc).timestamp()
        dates = ["2026-11-02", "2026-11-03"]
        bounds = [(t0, t0 + 86400), (t0 + 86400, t0 + 172800)]
        rows = [entry("P", "T", 60, True, "1", date="2026-11-02"), entry("P", "T", 30, False, "1", date="2026-11-03"),
                entry("P", "U", 20, None, "2", date="2026-11-03"), entry("P", "T", 999, True, "1", date="2026-11-04")]
        r = report.build(dates, bounds, {"aw": []}, rows, "2026-11-03")
        inv = r["invoice"]
        self.assertEqual(inv["minutes"], sum(d["logged"] for d in r["days"]))  # the day after the range is left out
        self.assertEqual(inv["billable"], sum(d["billable"] for d in r["days"]))
        self.assertEqual(inv["nonBillable"], sum(d["nonBillable"] for d in r["days"]))
        self.assertEqual(inv["billablePct"], 67)


class PdfPageTest(unittest.TestCase):
    """The page asks Pulsar's window for a PDF only when the window says it can: the updates replace the page and the
    server, never the window, so an older window gets the page of this release and must still work."""

    def test_the_download_menu_saves_pdfs_and_keeps_print(self):
        html = read("web", "index.html")
        menu = html[html.index('id="report-dl"'):html.index("</details>", html.index('id="report-dl"'))]
        self.assertEqual(re.findall(r'data-dl="([a-z-]+)"', menu), ["pdf", "invoice", "days", "entries", "invoice-csv", "print"])
        self.assertIn(">Report, PDF<", menu)
        self.assertIn(">Invoice summary, PDF<", menu)
        self.assertIn(">Print…<", menu)
        self.assertIn('class="invoice-doc" id="invoice-doc"', html)

    def test_the_window_writes_the_pdf_only_where_it_says_it_can(self):
        swift, native = read("macos", "PulsarWindow.swift"), read("web", "js", "native.js")
        self.assertIn('"features": ["savePDF"]', swift)
        self.assertIn('case "savePDF":', swift)
        self.assertIn("info.jobDisposition = .save", swift[swift.index("private func savePDF("):])
        self.assertIn('callPage("pdfSaved(', swift)
        self.assertRegex(native, r"SHELL\.features\.includes\('savePDF'\)\s*\?\s*name => new Promise")
        self.assertIn("send({action: 'savePDF', id, name})", native)
        self.assertRegex(native, r"window\.pulsarNative = \{[^}]*savePDF, pdfSaved")

    def test_an_older_window_and_a_browser_print_instead(self):
        js = read("web", "js", "report.js")
        save = js[js.index("async function savePDF("):js.index("$('#report-dl').addEventListener('click'")]
        self.assertIn("const save = window.pulsarNative?.savePDF;", save)
        self.assertRegex(save, r"if \(typeof save !== 'function'\) \{[\s\S]{0,200}printDialog\(what,")
        dialog = js[js.index("function printDialog("):js.index("async function savePDF(")]
        self.assertIn("if (APP) { toApp('print'); return; }", dialog)  # the window's print dialog, as before
        self.assertIn("window.print();", dialog)  # a browser's
        self.assertIn("if (dl === 'print') { printDialog('report', base); return; }", js)  # Print… never saves a file

    def test_the_windows_menus_reach_the_pages_items(self):
        swift, native, html = read("macos", "PulsarWindow.swift"), read("web", "js", "native.js"), read("web", "index.html")
        for run, dl in (("print", "print"), ("exportPDF", "pdf"), ("exportInvoice", "invoice"), ("exportDays", "days"),
                        ("exportEntries", "entries"), ("exportInvoiceCSV", "invoice-csv")):
            self.assertRegex(swift, rf'page\("[^"]+", "{run}"')
            self.assertIn(f"""{run}: () => {{ if (state.view === 'report') click('#report-dl [data-dl="{dl}"]'); }},""", native)
            self.assertIn(f'data-dl="{dl}"', html)

    def test_the_invoice_prints_alone_and_paper_is_light_in_the_dark_theme(self):
        css = read("web", "style.css")
        printing = css[css.index("@media print {"):]
        self.assertIn("html.print-invoice body > :not(#report), html.print-invoice #report > :not(.invoice-doc)", printing)
        self.assertIn(".print-title, .invoice-doc { display: none; }", css)  # never on the screen
        self.assertIn('html:root, html:root[data-theme], html:root:not([data-theme="light"])', printing)
        js = read("web", "js", "report.js")
        self.assertIn("document.documentElement.classList.toggle('print-invoice', what === 'invoice')", js)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class InvoiceCellsTest(unittest.TestCase):
    """report.js billOf under node: a line's Billable cell never guesses where Productive does not show the billing."""

    def bill(self, line, share=False):
        core, js = read("web", "js", "core.js"), read("web", "js", "report.js")
        dur = re.search(r"^function dur\(m\) \{.*?^\}", core, re.S | re.M).group(0)
        bill = js[js.index("function billOf("):js.index("\nconst hoursOf")]
        code = f"{dur}\n{bill}\nconsole.log(JSON.stringify(billOf({json.dumps(line)}, {json.dumps(share)})));"
        done = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_billable_not_billable_and_unknown(self):
        self.assertEqual(self.bill({"minutes": 90, "billable": 90, "nonBillable": 0, "unknown": 0}), "1 h 30 min")
        self.assertEqual(self.bill({"minutes": 30, "billable": 0, "nonBillable": 30, "unknown": 0}), "Not billable")
        self.assertEqual(self.bill({"minutes": 40, "billable": 0, "nonBillable": 0, "unknown": 40}), "Unknown")
        self.assertEqual(self.bill({"minutes": 70, "billable": 45, "nonBillable": 15, "unknown": 10}), "45 min, 10 min unknown")
        self.assertEqual(self.bill({"minutes": 25, "billable": 0, "nonBillable": 15, "unknown": 10}), "Not billable, 10 min unknown")

    def test_a_subtotal_says_its_share(self):
        self.assertEqual(self.bill({"minutes": 150, "billable": 120, "nonBillable": 30, "unknown": 0, "billablePct": 80}, True),
                         "2 h (80%)")


if __name__ == "__main__":
    unittest.main()
