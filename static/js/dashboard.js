(function () {
  let gridInstance = null;

  function saveLayout(gridEl, grid) {
    if (!gridEl || !grid) return;
    const kit = gridEl.dataset.kit || "default";
    const nodes = grid.engine.nodes.map(n => ({
      id: n.el?.getAttribute("gs-id") || "",
      x: n.x, y: n.y, w: n.w, h: n.h
    }));
    localStorage.setItem(`labbuilder-layout-${kit}`, JSON.stringify(nodes));
  }

  function loadLayout(gridEl, grid) {
    if (!gridEl || !grid) return;
    const kit = gridEl.dataset.kit || "default";
    const raw = localStorage.getItem(`labbuilder-layout-${kit}`);
    if (!raw) return;

    try {
      const saved = JSON.parse(raw);
      saved.forEach(item => {
        const el = gridEl.querySelector(`[gs-id="${item.id}"]`);
        if (el) {
          el.setAttribute("gs-x", item.x);
          el.setAttribute("gs-y", item.y);
          el.setAttribute("gs-w", item.w);
          el.setAttribute("gs-h", item.h);
        }
      });
      grid.load(
        saved.map(item => ({
          id: item.id, x: item.x, y: item.y, w: item.w, h: item.h
        }))
      );
    } catch (e) {
      console.warn("Could not load dashboard layout", e);
    }
  }

  function initDashboard() {
    const gridEl = document.querySelector('.grid-stack');
    if (!gridEl || typeof GridStack === "undefined") return;

    if (gridInstance) {
      try { gridInstance.destroy(false); } catch (e) {}
      gridInstance = null;
    }

    gridInstance = GridStack.init({
      float: true,
      cellHeight: 90,
      margin: 12,
      disableOneColumnMode: false,
      resizable: { handles: 'all' }
    }, gridEl);

    loadLayout(gridEl, gridInstance);

    gridInstance.on('change', function () {
      saveLayout(gridEl, gridInstance);
    });
  }

  document.addEventListener("DOMContentLoaded", initDashboard);
  document.body.addEventListener("htmx:afterSwap", function (evt) {
    if (evt.target && evt.target.id === "main-content") {
      initDashboard();
    }
  });

  window.resetDashboardLayout = function () {
    const gridEl = document.querySelector('.grid-stack');
    if (!gridEl) return;
    const kit = gridEl.dataset.kit || "default";
    localStorage.removeItem(`labbuilder-layout-${kit}`);
    location.reload();
  };
})();
