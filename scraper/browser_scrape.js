// Scrape Class Roster rooms from inside your logged-in browser, no cookie copying.
//
// 1. Sign in at https://classes.cornell.edu/browse/roster/FA26 with your NetID.
// 2. Open DevTools (Cmd+Option+I) -> Console, paste this whole file, press Enter.
// 3. It downloads rooms-FA26.json (~4 min, 1 request/second). Then run:
//      python3 scraper/import_browser_rooms.py ~/Downloads/rooms-FA26.json
//
// Mirrors parse_subject_page() in fetch_rooms.py.
(async () => {
  const term = location.pathname.match(/roster\/(\w+)/)?.[1] || "FA26";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const text = (el) => (el ? el.textContent.trim() : null);

  function parse(html) {
    const doc = new DOMParser().parseFromString(html, "text/html");
    return [...doc.querySelectorAll("ul.section")].flatMap((ul) => {
      const fav = ul.querySelector("[data-class-nbr]");
      if (!fav) return [];
      const course = text(ul.querySelector(".course-repeater"))?.split(/\s+/) || [];
      const meetings = [...ul.querySelectorAll("li.section-alt-details.dates")].map((li, i) => {
        const fac = li.querySelector("a.facility-search");
        const times = text(li.querySelector("time.time"))?.match(/([\d:]+[ap]m)\s*-\s*([\d:]+[ap]m)/);
        return {
          mtg_nbr: i + 1,
          pattern: text(li.querySelector(".pattern-only")),
          time_start: times?.[1] || null,
          time_end: times?.[2] || null,
          building: fac ? new URL(fac.href).searchParams.get("q") : null,
          facility: text(fac),
        };
      });
      return [{
        class_nbr: +fav.dataset.classNbr,
        subject: course[0] || null,
        catalog_nbr: course[1] || null,
        component: fav.dataset.ssrComponent,
        section: fav.dataset.section,
        meetings,
      }];
    });
  }

  const subjects = (await (await fetch(`/api/2.0/config/subjects.json?roster=${term}`)).json())
    .data.subjects.map((s) => s.value);
  const out = {};
  let mtgs = 0, rooms = 0;
  for (const [i, subject] of subjects.entries()) {
    await sleep(1100);
    const resp = await fetch(`/browse/roster/${term}/subject/${subject}`);
    out[subject] = resp.ok ? parse(await resp.text()) : [];
    for (const s of out[subject]) for (const m of s.meetings) { mtgs++; if (m.building) rooms++; }
    console.log(`[${i + 1}/${subjects.length}] ${subject}: ${rooms}/${mtgs} meetings with rooms so far`);
    if (mtgs >= 200 && rooms === 0) {
      console.error("No rooms visible even in this browser session. Are you signed in? Stopping.");
      return;
    }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(out)], { type: "application/json" }));
  a.download = `rooms-${term}.json`;
  a.click();
  console.log(`Done: ${rooms}/${mtgs} meetings with rooms. Saved rooms-${term}.json`);
})();
