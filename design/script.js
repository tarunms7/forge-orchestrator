// Phase Switcher
document.querySelectorAll('.phase-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.phase-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const phase = btn.dataset.phase;
    document.querySelectorAll('.phase-content').forEach(el => el.style.display = 'none');
    const target = document.getElementById('phase-' + phase);
    if (target) target.style.display = 'block';

    // Update progress stepper
    updateStepper(phase);

    // Recalculate arrows when switching to plan review
    if (phase === 'planned') {
      requestAnimationFrame(updateDependencyArrows);
    }
  });
});

function updateStepper(phase) {
  const steps = document.querySelectorAll('.step');
  const connectors = document.querySelectorAll('.step-connector');
  const progressFill = document.querySelector('.progress-bar-fill');
  const phases = ['planning', 'planned', 'executing', 'complete'];
  const idx = phases.indexOf(phase === 'error' ? 'complete' : phase);

  steps.forEach((step, i) => {
    step.classList.remove('completed', 'current');
    if (i < idx) step.classList.add('completed');
    else if (i === idx) step.classList.add('current');
  });

  connectors.forEach((conn, i) => {
    conn.classList.remove('done', 'active');
    if (i < idx) conn.classList.add('done');
    else if (i === idx) conn.classList.add('active');
  });

  const pcts = { planning: 10, planned: 25, executing: 50, complete: 100, error: 75 };
  progressFill.style.width = (pcts[phase] || 0) + '%';
}

// Task Card Click → Open Detail Panel
document.querySelectorAll('.task-card, .result-row').forEach(card => {
  card.addEventListener('click', () => {
    document.getElementById('detailPanel').classList.add('open');
    document.getElementById('detailOverlay').classList.add('visible');
  });
});

// Close Detail Panel
document.getElementById('detailClose').addEventListener('click', closePanel);
document.getElementById('detailOverlay').addEventListener('click', closePanel);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePanel(); });

function closePanel() {
  document.getElementById('detailPanel').classList.remove('open');
  document.getElementById('detailOverlay').classList.remove('visible');
}

// Detail Tabs
document.querySelectorAll('.detail-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.detail-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    tab.classList.add('active');
    const target = document.getElementById('tab-' + tab.dataset.tab);
    if (target) target.classList.add('active');
  });
});

// Dynamic Dependency Arrow Positioning
// Measures actual node positions and updates SVG line coordinates
function updateDependencyArrows() {
  const depGraph = document.querySelector('.dep-graph');
  if (!depGraph || depGraph.offsetParent === null) return;

  const arrowDivs = depGraph.querySelectorAll('.dep-arrows');

  function getNodeCenterX(taskNum) {
    const node = depGraph.querySelector(`.task-node[data-task="${taskNum}"]`);
    if (!node) return null;
    const rect = node.getBoundingClientRect();
    return rect.left + rect.width / 2;
  }

  // Tier 1 → Tier 2 arrows
  const svg1 = arrowDivs[0] && arrowDivs[0].querySelector('svg');
  if (svg1) {
    const svgRect = svg1.getBoundingClientRect();
    if (svgRect.width === 0) return;
    const toX = function(absX) {
      return ((absX - svgRect.left) / svgRect.width * 100).toFixed(1) + '%';
    };
    var lines = svg1.querySelectorAll('line');
    var t1 = getNodeCenterX(1), t2 = getNodeCenterX(2), t3 = getNodeCenterX(3), t4 = getNodeCenterX(4);
    if (lines[0] && t1 != null && t4 != null) { lines[0].setAttribute('x1', toX(t1)); lines[0].setAttribute('x2', toX(t4)); }
    if (lines[1] && t2 != null && t4 != null) { lines[1].setAttribute('x1', toX(t2)); lines[1].setAttribute('x2', toX(t4)); }
    if (lines[2] && t3 != null && t4 != null) { lines[2].setAttribute('x1', toX(t3)); lines[2].setAttribute('x2', toX(t4)); }
  }

  // Tier 2 → Tier 3 arrows
  var svg2 = arrowDivs[1] && arrowDivs[1].querySelector('svg');
  if (svg2) {
    var svgRect2 = svg2.getBoundingClientRect();
    if (svgRect2.width === 0) return;
    var toX2 = function(absX) {
      return ((absX - svgRect2.left) / svgRect2.width * 100).toFixed(1) + '%';
    };
    var lines2 = svg2.querySelectorAll('line');
    var t4b = getNodeCenterX(4), t5 = getNodeCenterX(5);
    if (lines2[0] && t4b != null && t5 != null) { lines2[0].setAttribute('x1', toX2(t4b)); lines2[0].setAttribute('x2', toX2(t5)); }
  }
}

// Run on resize and initial load
window.addEventListener('resize', function() { requestAnimationFrame(updateDependencyArrows); });
window.addEventListener('load', function() { requestAnimationFrame(updateDependencyArrows); });

// Plan Task Card Hover → Highlight Dep Graph Node
document.querySelectorAll('.plan-task-card').forEach(card => {
  card.addEventListener('mouseenter', () => {
    const taskId = card.dataset.task;
    document.querySelectorAll('.dep-node.task-node').forEach(node => {
      node.style.opacity = node.dataset.task === taskId ? '1' : '0.4';
      if (node.dataset.task === taskId) {
        node.style.borderColor = 'var(--accent)';
        node.style.background = 'var(--accent-glow)';
      }
    });
  });
  card.addEventListener('mouseleave', () => {
    document.querySelectorAll('.dep-node.task-node').forEach(node => {
      node.style.opacity = '1';
      node.style.borderColor = 'var(--border)';
      node.style.background = 'var(--bg-surface-2)';
    });
  });
});
