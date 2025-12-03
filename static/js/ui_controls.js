(function(){
  // UI hold-to-move controls. Attach to elements with `data-action`.
  // UI-only tuning: boost the linear feed slightly for snappier button response
  const FEED_MAX = window.__UI_MAX_FEED || 5000.0;
  // Apply a client-side multiplier so the UI buttons feel faster without
  // changing server-side defaults. Capped by FEED_MAX.
  const FEED_DEFAULT = Math.min(FEED_MAX, (window.__UI_LINEAR_FEED || window.__UI_DEFAULT_FEED || 300.0) * 1.5);
  const TICK_DEFAULT = window.__UI_DEFAULT_TICK || 0.02;
  const HOLD_THRESHOLD_MS = window.__UI_HOLD_THRESHOLD_MS || 150;
  const CLICK_SUPPRESS_MS = window.__UI_CLICK_SUPPRESS_MS || 350;

  function postJson(url, body){
    return fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  }

  function startMove(action){
    return postJson('/move_probe/start', { action: action, speed: FEED_DEFAULT, tick_s: TICK_DEFAULT });
  }
  function stopMove(action){
    return postJson('/move_probe/stop', { action: action });
  }
  function singleStep(action, step){
    return postJson('/move_probe', { direction: actionToDirection(action), step: step });
  }

  // Map template `data-action` to server `direction` for single-step endpoint
  function actionToDirection(action){
    const map = {
      'x-plus':'Xplus','x-minus':'Xminus','y-plus':'Yplus','y-minus':'Yminus',
      'z-plus':'Zplus','z-minus':'Zminus','rot-cw':'rotateClockwise','rot-ccw':'rotateCounterclockwise'
    };
    return map[action] || action;
  }

  function attach(){
    const buttons = document.querySelectorAll('[data-action]');
    // Step dropdown removed: do not query DOM for `#distance` anymore.
    const distance = null;
    // Only handle XYZ/Z movement here; rotation buttons are handled by `app.js`.
    const movementActions = new Set(['x-plus','x-minus','y-plus','y-minus','z-plus','z-minus']);
    let movementButtons = Array.from(buttons).filter(b=> movementActions.has(b.getAttribute('data-action')) );

    // Poll server for active continuous moves and disable conflicting controls
    let motionActive = false;
    async function pollStatus(){
      try{
        const r = await fetch('/move_probe/status');
        if(!r.ok) throw new Error('status');
        const j = await r.json();
        const active = Array.isArray(j.active) && j.active.length>0;
        if(active !== motionActive){
          motionActive = active;
          document.body.classList.toggle('motion-active', motionActive);
          movementButtons.forEach(b=> b.disabled = motionActive);
        }
      }catch(e){
        // ignore transient errors
      }finally{
        setTimeout(pollStatus, 300);
      }
    }
    pollStatus();

    movementButtons.forEach(btn=>{
      const action = btn.getAttribute('data-action');
      let holdActive = false;
      let lastHoldStart = 0;
      // For rotation actions we only support single-step clicks (match app.js).
      const isRotation = action === 'rot-cw' || action === 'rot-ccw';
      const HOLD_THRESHOLD = HOLD_THRESHOLD_MS; // local alias for readability

      // Start on mousedown / touchstart (only for non-rotation moves)
      const onStart = (ev)=>{
        if (isRotation) return; // rotations use single-step click only
        ev.preventDefault();
        if(holdActive) return;
        holdActive = true;
        lastHoldStart = performance.now();
        btn.classList.add('active-moving');
        startMove(action).catch(()=>{});
      };

      const onEnd = (ev)=>{
        if(!holdActive) return;
        holdActive = false;
        // clear timestamp after a short delay so click suppression still works
        setTimeout(()=>{ lastHoldStart = 0; }, 250);
        btn.classList.remove('active-moving');
        stopMove(action).catch(()=>{});
      };

      // Single click sends a single-step (useful for quick adjustments)
      const onClick = (ev)=>{
        ev.preventDefault();
        // if a continuous motion is active (locally held), ignore single-step clicks
        if(lastHoldStart && (performance.now() - lastHoldStart) > HOLD_THRESHOLD) return;
        // also ignore if server reports motion active
        if(document.body.classList.contains('motion-active')) return;
        // Use global default E-axis step instead of dropdown value
        const step = (window.__E_AXIS_DEFAULT_STEP != null) ? parseFloat(window.__E_AXIS_DEFAULT_STEP) : 1;
        // For rotation buttons, explicitly send a single-step rotate via singleStep
        singleStep(action, step).catch(()=>{});
      };

      btn.addEventListener('mousedown', onStart);
      btn.addEventListener('touchstart', onStart, {passive:false});
      document.addEventListener('mouseup', onEnd);
      document.addEventListener('touchend', onEnd);
      // Ensure stop on touchcancel / pointercancel / window blur (safety)
      document.addEventListener('touchcancel', onEnd);
      window.addEventListener('blur', ()=>{ if(holdActive) onEnd(); });

      btn.addEventListener('click', onClick);
    });
  }

  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', attach);
  else attach();
})();
