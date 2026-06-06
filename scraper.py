import argparse
import json
import re
from pathlib import Path
 
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright
 
 
START_URL = "https://www.taylorclerkofcourt.com/WebCaseManagement/"
COURT_NAME = "SUPERIOR COURT"
DEFAULT_SEARCH_NAME = "WALLER, MATTHEW"
DEFAULT_SCREENSHOT_DIR = "screenshots"
 
DOCKET_KEYWORDS = (
    "AMENDED",
    "CONVICTION",
    "COST",
    "CREDIT",
    "DETENTION",
    "DISCHARGE",
    "DISMISSAL",
    "DISPOSITION",
    "FEE",
    "FINE",
    "FIRST OFFENDER",
    "GUILTY",
    "IMPOSITION",
    "JAIL",
    "MODIFICATION",
    "MODIFIED",
    "MODIFY",
    "ORDER",
    "PAROLE",
    "PRISON",
    "PROBATION",
    "PROGRAM",
    "RE-SENTENCING",
    "RESTITUTION",
    "REVOCATION",
    "REVOKE",
    "SENTENCE",
    "SENTENCING",
    "SUPERVISION",
    "TERMINATION",
    "TIME SERVED",
    "TREATMENT",
    "VIOLATION",
    "VOP",
    "WARRANT",
)
 
 
def clean_text(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).replace("\xa0", " ")).strip()
 
 
def safe_filename(value):
    value = clean_text(value).lower()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "page"
 
 
class Screenshotter:
    def __init__(self, page, directory):
        self.page = page
        self.directory = Path(directory)
        self.index = 0
 
    def capture(self, label):
        self.index += 1
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{self.index:02d}-{safe_filename(label)}.png"
        self.page.screenshot(path=str(path), full_page=True)
        return path
 
 
def find_browser_executable():
    candidates = (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None
 
 
def blank_party(name):
    return {
        "party-id": "",
        "name": name,
        "alias": "",
        "date-of-birth": "",
        "address": "",
        "gender": "",
        "race": "",
        "height": "",
        "weight": "",
        "hair-color": "",
        "eye-color": "",
        "dl-number": "",
        "state-id": "",
        "other-id": "",
    }
 
 
def blank_sentencing(other):
    return {
        "charge-id": "",
        "license-suspension": "",
        "community-service": "",
        "probation": "",
        "jail-time": "",
        "jail-suspended": "",
        "fine": "",
        "fine-suspended": "",
        "court-cost": "",
        "court-cost-suspended": "",
        "total_assessment": "",
        "total_assessment_paid": "",
        "other": other,
    }
 
 
def table_rows(frame, selector):
    return frame.locator(selector).evaluate(
        """table => Array.from(table.querySelectorAll("tr")).map(row =>
            Array.from(row.querySelectorAll("th,td")).map(cell =>
                cell.innerText.replace(/\\u00a0/g, " ").replace(/\\s+/g, " ").trim()
            )
        )"""
    )
 
 
def dict_rows(frame, selector):
    rows = table_rows(frame, selector)
    if len(rows) < 2:
        return []
 
    headers = rows[0]
    output = []
    for row in rows[1:]:
        if not any(row):
            continue
        output.append(
            {
                headers[index]: row[index] if index < len(row) else ""
                for index in range(len(headers))
            }
        )
    return output
 
 
def result_rows(frame):
    return frame.locator("#CriminalPublicGrid").evaluate(
        """grid => Array.from(grid.querySelectorAll("tr")).map(row => {
            const img = row.querySelector("img[id^='img']");
            if (!img) return null;
            const cells = Array.from(row.querySelectorAll("td")).map(cell =>
                cell.innerText.replace(/\\u00a0/g, " ").replace(/\\s+/g, " ").trim()
            );
            const match = (img.getAttribute("onclick") || "").match(/GetChildGrid\\('([^']+)'/);
            return {
                childId: match ? match[1] : img.id.replace(/^img/, ""),
                imageId: img.id,
                caseNumber: cells[1] || "",
                defendant: cells[2] || "",
                judge: cells[3] || "",
                status: cells[4] || "",
                filedDate: cells[5] || "",
                courtDate: cells[6] || ""
            };
        }).filter(Boolean)"""
    )
 
 
def merge_value(existing, incoming):
    existing = clean_text(existing)
    incoming = clean_text(incoming)
    if not incoming or incoming == existing:
        return existing
    if not existing:
        return incoming
 
    parts = [part.strip() for part in existing.split(" | ")]
    if incoming in parts:
        return existing
    return f"{existing} | {incoming}"
 
 
def mapped_charges(frame, child_id):
    detail_selector = f"#div{child_id}"
    if frame.locator(f"{detail_selector} #ChargeGrid").count() == 0:
        return []
 
    rows = dict_rows(frame, f"{detail_selector} #ChargeGrid")
    grouped = {}
    for row in rows:
        charge_id = clean_text(row.get("Charge #"))
        charge = {
            "charge-id": charge_id,
            "offense-date": clean_text(row.get("Offense Date")),
            "description": clean_text(row.get("Offense")),
            "disposition": clean_text(row.get("Disposition Method")),
            "disposition-date": clean_text(row.get("Disposed On")),
            "severity": clean_text(row.get("Felony/Misdemeanor")),
            "statute": clean_text(row.get("Violation code")),
            "party-id": "",
        }
 
        if charge_id not in grouped:
            grouped[charge_id] = charge
            continue
 
        for key, value in charge.items():
            grouped[charge_id][key] = merge_value(grouped[charge_id].get(key), value)
 
    return list(grouped.values())
 
 
def mapped_notes(frame, child_id):
    detail_selector = f"#div{child_id}"
    if frame.locator(f"{detail_selector} #EventGrid").count() == 0:
        return []
 
    rows = dict_rows(frame, f"{detail_selector} #EventGrid")
    notes = []
    for row in rows:
        notes.append(
            {
                "date": clean_text(row.get("Date")),
                "text": clean_text(row.get("Type")),
            }
        )
    return notes
 
 
def docket_other(notes):
    matches = []
    for note in notes:
        text = clean_text(note.get("text"))
        upper_text = text.upper()
        if any(keyword in upper_text for keyword in DOCKET_KEYWORDS):
            date = clean_text(note.get("date"))
            matches.append(f"{date} - {text}" if date else text)
    return "\n".join(matches)
 
 
def mapped_case(frame, row, screenshots=None):
    child_id = row["childId"]
    image_id = row["imageId"]
 
    frame.locator(f"#{image_id}").click()
    frame.wait_for_function(
        """childId => {
            const detail = document.querySelector(`#div${childId}`);
            return detail && detail.innerText.trim().length > 0;
        }""",
        arg=child_id,
        timeout=30000,
    )
    frame.wait_for_function(
        "() => !window.jQuery || window.jQuery.active === 0",
        timeout=30000,
    )
    try:
        frame.wait_for_selector(f"#div{child_id} #EventGrid", timeout=5000)
    except PlaywrightTimeoutError:
        pass
 
    if screenshots:
        screenshots.capture(f"case {clean_text(row.get('caseNumber')) or child_id}")
 
    notes = mapped_notes(frame, child_id)
    return {
        "case": {
            "court-name": COURT_NAME,
            "court-id": COURT_NAME,
            "case-identifier": clean_text(row.get("caseNumber")),
            "filed-date": clean_text(row.get("filedDate")),
            "next-court-date": "",
            "active-warrant": "",
        },
        "parties": blank_party(clean_text(row.get("defendant"))),
        "charges": mapped_charges(frame, child_id),
        "sentencing": blank_sentencing(docket_other(notes)),
        "events": [],
        "notes": notes,
    }
 
 
def open_criminal_search(page, screenshots=None):
    page.goto(START_URL, wait_until="networkidle", timeout=60000)
    if screenshots:
        screenshots.capture("landing page")
 
    main_frame = next((frame for frame in page.frames if frame.url.endswith("/Main.aspx")), None)
    if main_frame is None:
        raise RuntimeError("Could not find the Main.aspx iframe on the landing page.")
 
    main_frame.locator("#hlCriminalSearch").click()
    page.wait_for_timeout(1000)
 
    search_frame = next(
        (frame for frame in page.frames if "search.aspx?search=criminal" in frame.url),
        None,
    )
    if search_frame is None:
        page.wait_for_function(
            """() => Array.from(window.frames).some(frame => {
                try { return frame.location.href.includes("search.aspx?search=criminal"); }
                catch (_) { return false; }
            })""",
            timeout=30000,
        )
        search_frame = next(
            (frame for frame in page.frames if "search.aspx?search=criminal" in frame.url),
            None,
        )
 
    if search_frame is None:
        raise RuntimeError("Could not open the Criminal Search page.")
 
    if screenshots:
        screenshots.capture("criminal search page")
    return search_frame
 
 
def perform_search(frame, case_number, name, screenshots=None):
    if case_number:
        frame.locator("#tbCaseNumber").fill(case_number)
    if name:
        frame.locator("#tbPersonSearch").fill(name)
 
    frame.locator("#cblCriminalCourtTypes_0").check()
    frame.locator("#btnSearch").click()
    frame.wait_for_function(
        """() => document.querySelector("#CriminalPublicGrid") ||
            document.body.innerText.includes("No results were found for your search")""",
        timeout=60000,
    )
 
    if screenshots:
        screenshots.capture("search results")
 
 
def scrape(args):
    browser_path = args.browser_path or find_browser_executable()
 
    case_number = clean_text(args.case_number)
    name = clean_text(args.name)
    if not name and (args.last or args.first):
        name = clean_text(f"{args.last or ''}, {args.first or ''}")
 
    if not case_number and not name:
        name = DEFAULT_SEARCH_NAME
        print(f"No search arguments supplied; searching for {DEFAULT_SEARCH_NAME}.")
 
    with sync_playwright() as playwright:
        launch_options = {"headless": args.headless}
        if browser_path:
            launch_options["executable_path"] = browser_path
 
        browser = playwright.chromium.launch(**launch_options)
        try:
            page = browser.new_page(viewport={"width": 1366, "height": 900})
            screenshots = Screenshotter(page, args.screenshot_dir)
            frame = open_criminal_search(page, screenshots=screenshots)
            perform_search(frame, case_number=case_number, name=name, screenshots=screenshots)
 
            body_text = frame.locator("body").inner_text()
            if "No results were found for your search" in body_text:
                return {"cases": []}
 
            rows = result_rows(frame)
            if args.max_cases is not None:
                rows = rows[: args.max_cases]
 
            return {"cases": [mapped_case(frame, row, screenshots=screenshots) for row in rows]}
        finally:
            browser.close()
 
 
def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape Taylor County, GA criminal case data from Icon Case Search."
    )
    parser.add_argument("--case-number", default="", help="Case number, for example 13-CR-132.")
    parser.add_argument("--name", default="", help='Full name search in "LAST, FIRST" format.')
    parser.add_argument("--last", default="", help="Last name for criminal name search.")
    parser.add_argument("--first", default="", help="First name for criminal name search.")
    parser.add_argument("--max-cases", type=int, default=None, help="Optional limit for expanded cases.")
    parser.add_argument("--output", default="taylor_results.json", help="JSON output path.")
    parser.add_argument("--screenshot-dir", default=DEFAULT_SCREENSHOT_DIR, help="Directory for navigation screenshots.")
    parser.add_argument("--browser-path", default="", help="Optional Chrome or Edge executable path.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. This site may block headless browsers with HTTP 403.",
    )
    return parser.parse_args()
 
 
def main():
    args = parse_args()
    try:
        data = scrape(args)
    except PlaywrightTimeoutError as error:
        raise SystemExit(f"Timed out while waiting for the court website: {error}") from error
 
    output_path = Path(args.output)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(json.dumps(data, indent=2))
    print(f"\nSaved {len(data['cases'])} case(s) to {output_path.resolve()}")
 
 
if __name__ == "__main__":
    main()
 