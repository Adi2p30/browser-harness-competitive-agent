(function () {
  if (!window.RUN_ID) return;
  const root = document.getElementById("steps");
  const statusEl = document.getElementById("run-status");
  const endedEl = document.getElementById("run-ended");
  const labels = window.PHASE_LABELS || {};
  const rows = [{ title: null, phases: {} }]; // bucket for lines before the first row card
  let lastSeq = 0;

  function render() {
    root.innerHTML = "";
    rows.forEach((row) => {
      const card = document.createElement("div");
      card.className = "row-card";
      if (row.title) {
        const h = document.createElement("div");
        h.className = "row-title";
        h.textContent = row.title;
        card.appendChild(h);
      }
      Object.keys(row.phases).forEach((phase) => {
        const group = document.createElement("div");
        group.className = "phase phase-" + phase;
        const label = document.createElement("div");
        label.className = "phase-label";
        label.textContent = labels[phase] || phase;
        group.appendChild(label);
        row.phases[phase].forEach((line) => {
          const p = document.createElement("div");
          p.className = "log-line level-" + (line.level || "info").toLowerCase();
          p.textContent = (line.ts ? line.ts + "  " : "") + line.message;
          group.appendChild(p);
        });
        card.appendChild(group);
      });
      root.appendChild(card);
    });
    root.scrollTop = root.scrollHeight;
  }

  function apply(event) {
    if (event.row_start) {
      rows.push({ title: event.message, phases: {} });
      return;
    }
    const row = rows[rows.length - 1];
    (row.phases[event.phase] = row.phases[event.phase] || []).push(event);
  }

  const source = new EventSource(`/run/${window.RUN_ID}/events?last=${lastSeq}`);
  source.addEventListener("line", (e) => {
    const data = JSON.parse(e.data);
    lastSeq = data.seq;
    apply(data);
    render();
  });
  source.addEventListener("end", (e) => {
    const data = JSON.parse(e.data);
    statusEl.textContent = data.status;
    statusEl.className = "status " + data.status;
    endedEl.hidden = false;
    endedEl.textContent = "Run ended: " + data.status + ".";
    source.close();
    setTimeout(() => window.location.reload(), 600); // reload once for the server-rendered decisions table
  });
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) {
      endedEl.hidden = false;
      endedEl.textContent = "Connection to the run closed.";
    }
  };
})();
