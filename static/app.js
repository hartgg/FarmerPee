(function () {
  const el = document.getElementById("harvestChart");
  if (!el || !window.__HARVEST_SERIES__) return;

  const labels = window.__HARVEST_SERIES__.map(x => x.month);
  const data = window.__HARVEST_SERIES__.map(x => x.tons);

  new Chart(el, {
    type: "bar",
    data: { labels, datasets: [{ label: "ตัน/เดือน", data }] },
    options: {
      responsive: true,
      plugins: { legend: { display: true } },
      scales: { y: { beginAtZero: true } }
    }
  });
})();
