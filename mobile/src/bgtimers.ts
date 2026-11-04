// Timers that keep running in the background (Android).
//
// React Native stops calling JS timers once the app leaves the screen (JavaTimerManager pauses on
// onHostPause), so with WinLink in the background its keep-alive ping every 4 s stopped. The computer
// heard nothing for 15 s and marked the phone offline, reconnected (the handshake still worked: socket
// data still arrives), and 15 s later dropped it again, over and over. Retries, read timeouts and the
// "computer went quiet" check stopped too.
//
// So setTimeout/setInterval are wrapped: each timer is kept in a list here as well as handed to React
// Native. While the app is in the background, a native clock ticks (see setTicking in the native
// module) and runDue() runs whatever is due. A timer fires once whichever way reaches it first.

interface Timer {
  at: number;            // when it's next due (ms since epoch)
  every: number;         // 0: a timeout; otherwise an interval's period
  fn: (...a: any[]) => void;
  args: any[];
  rn: any;               // React Native's own timer for it
}

const timers = new Map<number, Timer>();
let next = 2 ** 30;      // far from React Native's own ids, so a clear meant for one of those never hits ours
let installed = false;

const g = globalThis as any;
const rnSetTimeout = g.setTimeout;
const rnClearTimeout = g.clearTimeout;
const rnSetInterval = g.setInterval;
const rnClearInterval = g.clearInterval;

function fire(id: number) {
  const t = timers.get(id);
  if (!t) return;
  if (t.every) {
    t.at = Date.now() + t.every;
  } else {
    timers.delete(id);
    rnClearTimeout(t.rn);
  }
  try {
    t.fn(...t.args);
  } catch (e) {
    // the same as an uncaught error in a timer: report it, keep the other timers going
    rnSetTimeout(() => {
      throw e;
    }, 0);
  }
}

/** Run every timer that's due. Called on each tick of the native clock while in the background. */
export function runDue() {
  const now = Date.now();
  const due: number[] = [];
  for (const [id, t] of timers) if (t.at <= now) due.push(id);
  due.sort((a, b) => timers.get(a)!.at - timers.get(b)!.at);
  for (const id of due) {
    const t = timers.get(id);
    if (t && t.at <= now) fire(id);      // still there and still due (an earlier one may have cleared it)
  }
}

export function installBackgroundTimers() {
  if (installed) return;
  installed = true;
  g.setTimeout = (fn: any, ms?: number, ...args: any[]) => {
    if (typeof fn !== "function") return rnSetTimeout(fn, ms, ...args);
    const delay = Math.max(0, Number(ms) || 0);
    const id = next++;
    const t: Timer = { at: Date.now() + delay, every: 0, fn, args, rn: null };
    timers.set(id, t);
    t.rn = rnSetTimeout(() => fire(id), delay);
    return id;
  };
  g.setInterval = (fn: any, ms?: number, ...args: any[]) => {
    if (typeof fn !== "function") return rnSetInterval(fn, ms, ...args);
    const period = Math.max(1, Number(ms) || 0);
    const id = next++;
    const t: Timer = { at: Date.now() + period, every: period, fn, args, rn: null };
    timers.set(id, t);
    // React Native's interval: skip a firing that comes early because the clock here already ran it
    t.rn = rnSetInterval(() => {
      const cur = timers.get(id);
      if (cur && Date.now() >= cur.at - period / 2) fire(id);
    }, period);
    return id;
  };
  const clear = (id: any) => {
    const t = timers.get(id);
    if (!t) return false;
    timers.delete(id);
    if (t.every) rnClearInterval(t.rn);
    else rnClearTimeout(t.rn);
    return true;
  };
  g.clearTimeout = (id: any) => {
    if (!clear(id)) rnClearTimeout(id);
  };
  g.clearInterval = (id: any) => {
    if (!clear(id)) rnClearInterval(id);
  };
}
