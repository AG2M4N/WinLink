import "./src/polyfills";
import * as DocumentPicker from "expo-document-picker";
import * as Linking from "expo-linking";
import { ShareIntentProvider, useShareIntentContext } from "expo-share-intent";
import * as Sharing from "expo-sharing";
import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  ActivityIndicator, Alert, GestureResponderEvent, Image, Modal, PanResponder, Platform, Pressable, ScrollView,
  StyleSheet, Switch, Text, TextInput, useColorScheme, View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import Native from "./modules/winlink-native";
import * as Clipboard from "expo-clipboard";
import { AutoClipView, ClipItem, controller, DeviceView, Snapshot, TransferView } from "./src/controller";

// ---------------------------------------------------------------- look
const LIGHT = { bg: "#F4F4F2", card: "#FFFFFF", raised: "#EFEFEC", text: "#1C1C1E", text2: "#6C6C70", text3: "#9A9AA0",
  line: "rgba(0,0,0,0.08)", accent: "#3E5F8A", onAccent: "#FFFFFF", accentSoft: "rgba(62,95,138,0.10)", good: "#3F8A5C",
  warn: "#9A6B26", bad: "#B4483C", track: "#DCDCD8" };
const DARK = { bg: "#171719", card: "#222225", raised: "#2B2B2F", text: "#F2F2F3", text2: "#9B9BA1", text3: "#6E6E74",
  line: "rgba(255,255,255,0.08)", accent: "#8FAAD3", onAccent: "#111214", accentSoft: "rgba(143,170,211,0.14)", good: "#7BB592",
  warn: "#D1A35F", bad: "#E08F82", track: "#3A3A3E" };
type Colors = typeof LIGHT;

const useSnapshot = () => {
  const cache = useRef<Snapshot>(controller.snapshot());
  return useSyncExternalStore(
    (f) => controller.subscribe(() => {
      cache.current = controller.snapshot();
      f();
    }),
    () => cache.current,
  );
};

export default function App() {
  return (
    <SafeAreaProvider>
      <ShareIntentProvider options={{ resetOnBackground: true }}>
        <Main />
      </ShareIntentProvider>
    </SafeAreaProvider>
  );
}

type Tab = "devices" | "files" | "clipboard" | "sound" | "settings";

function Main() {
  const scheme = useColorScheme();
  const c = scheme === "dark" ? DARK : LIGHT;
  const st = useMemo(() => styles(c), [c]);
  const snap = useSnapshot();
  const [tab, setTab] = useState<Tab>("devices");
  const [notice, setNotice] = useState<string | null>(null);
  const share = useShareIntentContext();
  const url = Linking.useURL();

  useEffect(() => {
    void controller.start();
  }, []);

  useEffect(() => {
    if (!snap.notice) return;
    setNotice(snap.notice.text);
    const t = setTimeout(() => setNotice(null), 5000);
    return () => clearTimeout(t);
  }, [snap.notice?.at]);

  // winlink://send-clipboard (an iPhone Shortcut on Back Tap, or any automation)
  useEffect(() => {
    if (!url || !/send-clipboard/.test(url)) return;
    let tries = 0;
    const t = setInterval(() => {
      tries += 1;
      if (controller.node?.connected().length || tries > 16) {
        clearInterval(t);
        void controller.sendClipboard();
      }
    }, 500);
    return () => clearInterval(t);
  }, [url]);

  const counts = {
    devices: snap.devices.filter((d) => d.state === "pairing").length,
    files: snap.transfers.filter((t) => t.state === "active").length,
  };

  return (
    <SafeAreaView style={st.safe}>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
      <View style={st.header}>
        <Text style={st.brand}>WinLink</Text>
        <Text style={st.headerSub} numberOfLines={1}>
          {snap.devices.filter((d) => d.state === "on").length
            ? `Connected to ${snap.devices.filter((d) => d.state === "on").map((d) => d.name).join(", ")}`
            : "Not connected"}
        </Text>
      </View>
      {notice ? (
        <Pressable onPress={() => setNotice(null)} style={st.toast}>
          <Text style={st.toastText}>{notice}</Text>
        </Pressable>
      ) : null}
      {share.hasShareIntent ? <ShareSheet c={c} st={st} snap={snap} /> : null}
      {snap.trackpad ? <Trackpad c={c} st={st} pad={snap.trackpad} /> : null}
      <ScrollView contentContainerStyle={st.page} keyboardShouldPersistTaps="handled">
        {!snap.ready ? <ActivityIndicator color={c.accent} style={{ marginTop: 40 }} /> : null}
        {snap.ready && tab === "devices" ? <Devices c={c} st={st} snap={snap} /> : null}
        {snap.ready && tab === "files" ? <Files c={c} st={st} snap={snap} /> : null}
        {snap.ready && tab === "clipboard" ? <ClipboardPage c={c} st={st} snap={snap} /> : null}
        {snap.ready && tab === "sound" ? <Sound c={c} st={st} snap={snap} /> : null}
        {snap.ready && tab === "settings" ? <Settings c={c} st={st} snap={snap} /> : null}
      </ScrollView>
      <View style={st.tabs}>
        {(["devices", "files", "clipboard", "sound", "settings"] as Tab[]).map((t) => (
          <Pressable key={t} onPress={() => setTab(t)} style={st.tab} accessibilityRole="tab" accessibilityState={{ selected: tab === t }}>
            <View style={[st.tabPill, tab === t && st.tabPillOn]}>
              <Image source={TAB_ICONS[t]} style={[st.tabIcon, { tintColor: tab === t ? c.accent : c.text2 }]} />
              {t in counts && counts[t as "devices" | "files"] ? <View style={[st.badge, st.tabBadge]}><Text style={st.badgeText}>{counts[t as "devices" | "files"]}</Text></View> : null}
            </View>
            <Text style={[st.tabText, tab === t && st.tabTextOn]}>{LABELS[t]}</Text>
          </Pressable>
        ))}
      </View>
    </SafeAreaView>
  );
}

const TAB_ICONS = {
  devices: require("./assets/tabs/devices.png"),
  files: require("./assets/tabs/files.png"),
  clipboard: require("./assets/tabs/clipboard.png"),
  sound: require("./assets/tabs/sound.png"),
  settings: require("./assets/tabs/settings.png"),
} as const;
const LABELS: Record<Tab, string> = { devices: "Devices", files: "Files", clipboard: "Clipboard", sound: "Sound", settings: "Settings" };

interface PageProps {
  c: Colors;
  st: ReturnType<typeof styles>;
  snap: Snapshot;
}

// ---------------------------------------------------------------- pieces
function Button({ st, title, onPress, kind = "plain", disabled }: { st: PageProps["st"]; title: string; onPress: () => void; kind?: "plain" | "primary" | "quiet" | "danger"; disabled?: boolean }) {
  return (
    <Pressable onPress={onPress} disabled={disabled} style={({ pressed }) => [st.btn, kind === "primary" && st.btnPrimary,
      kind === "quiet" && st.btnQuiet, pressed && { opacity: 0.7 }, disabled && { opacity: 0.45 }]}>
      <Text style={[st.btnText, kind === "primary" && st.btnPrimaryText, kind === "quiet" && st.btnQuietText,
        kind === "danger" && st.btnDangerText]}>{title}</Text>
    </Pressable>
  );
}

function Chips<T extends string>({ st, options, value, onChange }: { st: PageProps["st"]; options: [T, string][]; value: T | null; onChange: (v: T) => void }) {
  return (
    <View style={st.chips}>
      {options.map(([v, label]) => (
        <Pressable key={v} onPress={() => onChange(v)} style={[st.chip, v === value && st.chipOn]}>
          <Text style={[st.chipText, v === value && st.chipTextOn]} numberOfLines={1}>{label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function Section({ st, title, children, right }: { st: PageProps["st"]; title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <View style={{ marginTop: 22 }}>
      <View style={st.sectionHead}>
        <Text style={st.h2}>{title}</Text>
        {right}
      </View>
      {children}
    </View>
  );
}

const osLabel = (os: string) => ({ mac: "Mac", windows: "Windows PC", linux: "Linux", android: "Android", ios: "iPhone" } as Record<string, string>)[os] ?? "Computer";
const ago = (ts: number) => {
  const s = (Date.now() - ts) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return new Date(ts).toLocaleDateString([], { day: "numeric", month: "short" });
};
const human = (n: number) => (n >= 2 ** 30 ? `${(n / 2 ** 30).toFixed(1)} GB` : n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

// ---------------------------------------------------------------- devices
function Devices({ c, st, snap }: PageProps) {
  const [addr, setAddr] = useState("");
  const pairing = snap.devices.filter((d) => d.state === "pairing");
  return (
    <View>
      <Text style={st.h1}>Devices</Text>
      <Text style={st.lead}>Your Macs and PCs running WinLink. On the same Wi‑Fi they show up by themselves.</Text>
      {pairing.map((d) => (
        <View key={d.id} style={[st.card, st.ask]}>
          <Text style={st.name}>Pairing with {d.name}</Text>
          <Text style={st.sub}>Check that {d.name} shows this code, then press Allow there.</Text>
          <Text style={st.code}>{d.code}</Text>
        </View>
      ))}
      {snap.devices.filter((d) => d.state !== "pairing").length === 0 ? (
        <View style={[st.card, st.empty]}>
          <ActivityIndicator color={c.accent} />
          <Text style={[st.name, { marginTop: 10 }]}>Looking for your computers…</Text>
          <Text style={[st.sub, { textAlign: "center" }]}>Open WinLink on your Mac or PC, on the same Wi‑Fi as this phone.</Text>
        </View>
      ) : null}
      {snap.devices.filter((d) => d.state !== "pairing").map((d) => <DeviceRow key={d.id} c={c} st={st} d={d} />)}
      <Section st={st} title="Away from home">
        {snap.anywhere?.available ? <AnywhereCard c={c} st={st} snap={snap} /> : <TailscaleCard c={c} st={st} snap={snap} />}
        <View style={st.card}>
          <Text style={st.sub}>Computers on your Tailscale account show up by themselves. To reach one by hand, enter its Tailscale address.</Text>
          <View style={[st.row, { marginTop: 10 }]}>
            <TextInput value={addr} onChangeText={setAddr} placeholder="100.101.102.103" placeholderTextColor={c.text3}
              autoCapitalize="none" autoCorrect={false} keyboardType="numbers-and-punctuation" style={st.input} />
            <Button st={st} title="Connect" kind="primary" disabled={!addr.trim()} onPress={() => { void controller.addByAddress(addr); setAddr(""); }} />
          </View>
        </View>
        <View style={{ alignItems: "flex-start", marginTop: 8 }}>
          <Button st={st} title="Search again" kind="quiet" onPress={() => controller.rescan()} />
        </View>
      </Section>
    </View>
  );
}

function AnywhereCard({ c, st, snap }: PageProps) {
  const a = snap.anywhere!;
  const expiry = a.keyExpiry ? new Date(a.keyExpiry) : null;
  const soon = expiry && expiry.getTime() - Date.now() < 14 * 86400000;
  let text: string;
  let tone: "good" | "bad" | null = null;
  if (!a.on) text = "Reach your computers over mobile data or any Wi‑Fi. Tailscale is built in: sign in once with the same account as your computers.";
  else if (a.state === "Error") { text = a.error; tone = "bad"; }
  else if (a.state === "Starting") text = "Starting…";
  else if (a.state === "NeedsLogin") text = a.signingIn ? "Finish signing in in the browser, then come back here." : "Sign in to Tailscale with the same account as your computers. You only do this once.";
  else if (a.state === "NeedsMachineAuth") text = "Waiting for your Tailscale admin to approve this phone (login.tailscale.com › Machines).";
  else {
    tone = "good";
    text = (a.computers.length ? `Reaching ${a.computers.join(", ")} from anywhere.` : "On. Turn on Connect from anywhere on your computers too, with the same account.")
      + ` This phone is ${a.name || "on your tailnet"}${a.ips[0] ? ` (${a.ips[0]})` : ""}.`;
  }
  return (
    <View style={st.card}>
      <View style={st.row}>
        <View style={{ flex: 1 }}>
          <Text style={st.name}>Connect from anywhere</Text>
          <Text style={[st.sub, tone === "good" && { color: c.good }, tone === "bad" && { color: c.bad }]}>{text}</Text>
          {a.state === "Running" && expiry ? (
            <Text style={[st.sub, soon && { color: c.warn }]}>
              {soon ? "Sign-in expires" : "Signed in until"} {expiry.toLocaleDateString()}{soon ? ". Sign out and in again to renew." : ""}
            </Text>
          ) : null}
        </View>
        {a.state === "Starting" && a.on ? <ActivityIndicator color={c.accent} /> : null}
        <Switch value={a.on} onValueChange={(v) => controller.setAnywhere(v)} trackColor={{ true: c.accent }} />
      </View>
      {a.on && (a.state === "NeedsLogin" || a.state === "Error" || a.state === "Running") ? (
        <View style={[st.row, { marginTop: 10, gap: 8 }]}>
          {a.state === "NeedsLogin" ? <Button st={st} title={a.signingIn ? "Open sign-in again" : "Sign in"} kind="primary" onPress={() => void controller.anywhereSignIn()} /> : null}
          {a.state === "Error" ? <Button st={st} title="Try again" kind="primary" onPress={() => controller.anywhereRetry()} /> : null}
          {a.state === "Running" ? (
            <Button st={st} title="Sign out" kind="quiet" onPress={() => Alert.alert("Sign out of Tailscale?",
              "This phone leaves your tailnet and forgets its Tailscale identity. You can sign in again any time.",
              [{ text: "Cancel", style: "cancel" }, { text: "Sign out", style: "destructive", onPress: () => void controller.anywhereSignOut() }])} />
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function TailscaleCard({ c, st, snap }: PageProps) {
  const ts = snap.tailscale;
  const reachable = snap.devices.filter((d) => d.anywhere).map((d) => d.name);
  const [title, text, button] = ts.on
    ? ["Tailscale is on", reachable.length
        ? `${reachable.join(", ")} can be reached from any network. This phone is ${ts.ips[0]}.`
        : `This phone is ${ts.ips[0]}. Turn on Tailscale on your computers too, signed in to the same account.`, "Open Tailscale"]
    : ts.installed === false
      ? ["Use WinLink on any network", "Install the free Tailscale app and sign in with the same account as your computers. WinLink then reaches them over mobile data or any Wi‑Fi.", "Get Tailscale"]
      : ["Tailscale is off", "Turn it on in the Tailscale app to reach your computers from other networks. WinLink switches over by itself.",
         Platform.OS === "ios" ? "Get Tailscale" : "Open Tailscale"];
  return (
    <View style={st.card}>
      <View style={st.row}>
        <View style={{ flex: 1 }}>
          <Text style={st.name}>{title}</Text>
          <Text style={[st.sub, ts.on && { color: c.good }]}>{text}</Text>
        </View>
      </View>
      <View style={[st.row, { marginTop: 10 }]}>
        <Button st={st} title={button} kind={ts.on ? "quiet" : "primary"} onPress={() => controller.openTailscale()} />
      </View>
    </View>
  );
}

// ---------------------------------------------------------------- trackpad
/** The middle of the fingers on the pad (one finger: that finger). */
function centre(e: GestureResponderEvent) {
  const t = e.nativeEvent.touches;
  if (!t.length) return { x: e.nativeEvent.locationX, y: e.nativeEvent.locationY, n: 0 };
  let x = 0;
  let y = 0;
  for (const p of t) {
    x += p.pageX;
    y += p.pageY;
  }
  return { x: x / t.length, y: y / t.length, n: t.length };
}

const TAP_MS = 250;        // a touch shorter than this, that barely moved, is a tap
const TAP_MOVE = 10;       // points
const DOUBLE_MS = 350;     // a second touch this soon after a tap: double click, or tap-and-drag

/** Full screen: the phone becomes a touchpad for one computer.
 *  One finger moves the pointer · tap: click · two-finger tap: right click · two fingers: scroll ·
 *  tap, then touch again and move: drag. */
function Trackpad({ c, st, pad }: { c: Colors; st: PageProps["st"]; pad: NonNullable<Snapshot["trackpad"]> }) {
  const g = useRef({ n: 0, most: 0, start: 0, moved: 0, x: 0, y: 0, t: 0, lastTap: 0, second: false, dragging: false }).current;
  const responder = useMemo(() => {
    const end = () => {
      const now = Date.now();
      if (g.dragging) {
        controller.pointerButton("left", false);
        g.dragging = false;
        g.lastTap = 0;
      } else if (g.moved < TAP_MOVE && now - g.start < TAP_MS) {
        if (g.most >= 2) {
          controller.pointerClick("right", 1);
          g.lastTap = 0;
        } else {
          controller.pointerClick("left", g.second ? 2 : 1);
          g.lastTap = g.second ? 0 : now;
        }
      } else {
        g.lastTap = 0;
      }
      g.n = 0;
    };
    return PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onMoveShouldSetPanResponder: () => true,
      onPanResponderTerminationRequest: () => false,
      onPanResponderGrant: (e) => {
        const p = centre(e);
        Object.assign(g, { n: p.n, most: p.n, start: Date.now(), moved: 0, x: p.x, y: p.y, t: Date.now(),
          second: Date.now() - g.lastTap < DOUBLE_MS, dragging: false });
      },
      onPanResponderMove: (e) => {
        const p = centre(e);
        if (!p.n) return;
        if (p.n !== g.n) {                 // a finger joined or left: start again from here, no jump
          g.n = p.n;
          g.most = Math.max(g.most, p.n);
          g.x = p.x;
          g.y = p.y;
          return;
        }
        const now = Date.now();
        const dx = p.x - g.x;
        const dy = p.y - g.y;
        const dt = Math.max(1, now - g.t);
        g.x = p.x;
        g.y = p.y;
        g.t = now;
        g.moved += Math.abs(dx) + Math.abs(dy);
        if (g.n >= 2) {
          controller.pointerScroll(dx, dy);
          return;
        }
        if (g.second && !g.dragging && g.moved > 6) {     // tap, then touch and move: drag
          g.dragging = true;
          controller.pointerButton("left", true);
        }
        const speed = Math.hypot(dx, dy) / dt;           // points per ms: fast swipes go further
        const boost = 1 + Math.min(2.5, speed * 1.2);
        controller.pointerMove(dx * boost, dy * boost);
      },
      onPanResponderRelease: end,
      onPanResponderTerminate: end,
    });
  }, []);
  return (
    <Modal visible animationType="slide" onRequestClose={() => controller.closeTrackpad()} supportedOrientations={["portrait", "landscape"]}>
      <View style={{ flex: 1, backgroundColor: c.bg, paddingTop: 44, paddingBottom: 24, paddingHorizontal: 16 }}>
        <View style={[st.row, { marginBottom: 12 }]}>
          <View style={{ flex: 1 }}>
            <Text style={st.name}>{pad.name || "Computer"}</Text>
            <Text style={[st.sub, { color: pad.on ? c.good : c.warn }]}>{pad.on ? "Trackpad" : "Not connected"}</Text>
          </View>
          <Button st={st} title="Done" kind="quiet" onPress={() => controller.closeTrackpad()} />
        </View>
        <View {...responder.panHandlers}
          style={{ flex: 1, borderRadius: 18, backgroundColor: c.card, borderWidth: 1, borderColor: c.line,
            alignItems: "center", justifyContent: "center", padding: 24 }}>
          <Text style={[st.hint, { textAlign: "center" }]} pointerEvents="none">
            Move with one finger · tap to click · two-finger tap to right-click · two fingers to scroll · tap, then touch again and move to drag
          </Text>
        </View>
        <View style={[st.row, { marginTop: 12, gap: 12 }]}>
          <Pressable style={{ flex: 1, height: 56, borderRadius: 14, backgroundColor: c.raised, alignItems: "center", justifyContent: "center" }}
            onPressIn={() => controller.pointerButton("left", true)} onPressOut={() => controller.pointerButton("left", false)}>
            <Text style={st.name}>Left</Text>
          </Pressable>
          <Pressable style={{ flex: 1, height: 56, borderRadius: 14, backgroundColor: c.raised, alignItems: "center", justifyContent: "center" }}
            onPress={() => controller.pointerClick("right", 1)}>
            <Text style={st.name}>Right</Text>
          </Pressable>
        </View>
      </View>
    </Modal>
  );
}

function DeviceRow({ c, st, d }: { c: Colors; st: PageProps["st"]; d: DeviceView }) {
  const status = d.state === "on" ? `Connected · ${d.route === "tailscale" ? "Tailscale" : "Wi‑Fi"}`
    : d.state === "connecting" ? "Connecting…" : d.state === "available" ? (d.trusted ? "Nearby" : "Not paired")
    : d.error ? `Offline · ${d.error}` : "Offline";
  return (
    <View style={st.card}>
      <View style={st.row}>
        <View style={{ flex: 1 }}>
          <Text style={st.name}>{d.name}</Text>
          <Text style={[st.sub, d.state === "on" && { color: c.good }]} numberOfLines={2}>{osLabel(d.os)} · {status}</Text>
        </View>
        {d.state === "connecting" ? <ActivityIndicator color={c.accent} /> : null}
      </View>
      <View style={[st.row, { marginTop: 10, gap: 8 }]}>
        {d.state === "on" && !["android", "ios"].includes(d.os) ? (
          <Button st={st} title="Trackpad" kind="primary" onPress={() => controller.openTrackpad(d.id)} />
        ) : null}
        {d.state === "on" ? <Button st={st} title="Disconnect" kind="quiet" onPress={() => controller.disconnect(d.id)} /> : null}
        {d.state === "available" || (d.state === "away" && d.trusted) ? (
          <Button st={st} title={d.trusted ? "Connect" : "Pair"} kind="primary" onPress={() => controller.connect(d.id)} />
        ) : null}
        {d.trusted && d.state !== "on" ? (
          <Button st={st} title="Forget" kind="quiet" onPress={() => Alert.alert(`Forget ${d.name}?`, "You'll need to pair again to use it.", [
            { text: "Cancel", style: "cancel" }, { text: "Forget", style: "destructive", onPress: () => controller.forget(d.id) }])} />
        ) : null}
      </View>
    </View>
  );
}

// ---------------------------------------------------------------- files
function targetOptions(snap: Snapshot): [string, string][] {
  const on = snap.devices.filter((d) => d.state === "on").map((d) => [d.id, d.name] as [string, string]);
  return on.length > 1 ? [...on, ["all", "All devices"]] : on;
}

function Files({ c, st, snap }: PageProps) {
  const opts = targetOptions(snap);
  const [target, setTarget] = useState<string | null>(null);
  const chosen = opts.find(([id]) => id === target)?.[0] ?? opts[0]?.[0] ?? null;
  const pick = async () => {
    if (!chosen) return;
    let res: DocumentPicker.DocumentPickerResult;
    try {
      res = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    } catch (e) {
      Alert.alert("Couldn't open the file picker", (e as Error).message);
      return;
    }
    if (res.canceled) return;
    controller.sendFiles(chosen, res.assets.map((a) => ({ uri: a.uri, name: a.name, size: a.size })));
  };
  return (
    <View>
      <Text style={st.h1}>Files</Text>
      <Text style={st.lead}>Send files to a computer's Downloads folder. Files sent here are saved in {Platform.OS === "android" ? "Downloads › WinLink" : "Files › On My iPhone › WinLink"}.</Text>
      <View style={st.card}>
        {opts.length ? (
          <>
            <Text style={st.label}>Send to</Text>
            <Chips st={st} options={opts} value={chosen} onChange={setTarget} />
            <View style={{ marginTop: 12, alignItems: "flex-start" }}>
              <Button st={st} title="Choose files" kind="primary" onPress={pick} />
            </View>
            <Text style={[st.hint, { marginTop: 10 }]}>Or share from any app: Share › WinLink.</Text>
          </>
        ) : <Text style={st.sub}>Connect to a computer to send files.</Text>}
      </View>
      <Section st={st} title="Transfers">
        <View style={st.card}>
          {snap.transfers.length ? snap.transfers.map((t, i) => <TransferRow key={t.key} c={c} st={st} t={t} first={i === 0} />)
            : <Text style={st.sub}>No transfers yet.</Text>}
        </View>
      </Section>
      <Section st={st} title="Received">
        <View style={st.card}>
          {snap.received.length ? snap.received.slice(0, 50).map((r, i) => (
            <View key={r.uri} style={[st.listRow, i > 0 && st.sep]}>
              <View style={{ flex: 1 }}>
                <Text style={st.name} numberOfLines={1}>{r.name}</Text>
                <Text style={st.sub}>from {r.from} · {human(r.size)} · {ago(r.at)}</Text>
              </View>
              <Button st={st} title="Open" kind="quiet" onPress={() => openReceived(r.uri)} />
              <Button st={st} title="Delete" kind="danger" onPress={() => controller.deleteReceived(r.uri)} />
            </View>
          )) : <Text style={st.sub}>Files your computers send appear here.</Text>}
        </View>
      </Section>
    </View>
  );
}

async function openReceived(uri: string) {
  try {
    if (Platform.OS === "android") await Native.openSaved(uri, null);
    else await Sharing.shareAsync(uri);
  } catch (e) {
    Alert.alert("Couldn't open it", (e as Error).message);
  }
}

function TransferRow({ c, st, t, first }: { c: Colors; st: PageProps["st"]; t: TransferView; first: boolean }) {
  const state = t.state === "active" ? `${Math.round(t.frac * 100)}%${t.error ? ` · ${t.error}` : ""}`
    : t.state === "done" ? (t.dir === "in" ? "Saved" : "Sent") : t.state === "cancelled" ? (t.error === "cancelled" ? "Cancelled" : `Cancelled ${t.error?.replace("cancelled ", "") ?? ""}`)
    : `Failed${t.error ? `: ${t.error}` : ""}`;
  return (
    <View style={[st.listRow, !first && st.sep, { alignItems: "flex-start" }]}>
      <View style={{ flex: 1 }}>
        <Text style={st.name} numberOfLines={1}>{t.dir === "in" ? "↓ " : "↑ "}{t.name}</Text>
        <Text style={[st.sub, t.state === "failed" && { color: c.bad }, t.state === "done" && { color: c.good }]} numberOfLines={2}>
          {t.dir === "in" ? "from" : "to"} {t.peer}{t.files ? ` · ${t.files}` : ""} · {state}
        </Text>
        {t.state === "active" ? <View style={st.bar}><View style={[st.barFill, { width: `${Math.max(2, t.frac * 100)}%` }]} /></View> : null}
      </View>
      {t.state === "active" ? <Button st={st} title="Cancel" kind="danger" onPress={() => controller.cancel(t.key)} />
        : <Button st={st} title="✕" kind="quiet" onPress={() => controller.dismiss(t.key)} />}
    </View>
  );
}

// ---------------------------------------------------------------- clipboard
/** Android: "Send copies automatically", with the one-time setup it needs. */
function AutoClipCard({ c, st, a }: { c: PageProps["c"]; st: PageProps["st"]; a: AutoClipView }) {
  const cmd = `adb shell pm grant ${a.pkg} android.permission.READ_LOGS`;
  const ready = a.logs && a.overlay;
  return (
    <View style={st.card}>
      <View style={st.row}>
        <View style={{ flex: 1 }}>
          <Text style={st.name}>Send copies automatically</Text>
          <Text style={st.sub}>Advanced. Everything you copy on this phone goes to your computers, no tap needed.</Text>
        </View>
        <Switch value={a.on} onValueChange={(v) => controller.setAutoClip(v)} trackColor={{ true: c.accent }} />
      </View>
      {a.on && !a.logs ? (
        <View style={{ marginTop: 12 }}>
          <Text style={st.sub}>1. Android only allows this with a permission you give once from a computer. Turn on USB debugging (Settings › Developer options), connect the phone, and run:</Text>
          <Text selectable style={[st.log, { marginTop: 6 }]}>{cmd}</Text>
          <View style={{ marginTop: 8, alignItems: "flex-start" }}>
            <Button st={st} title="Copy command" kind="quiet" onPress={() => void Clipboard.setStringAsync(cmd)} />
          </View>
          <Text style={[st.sub, { marginTop: 6 }]}>Then close WinLink fully and open it again. If Android asks whether WinLink may read the device logs, allow it.</Text>
        </View>
      ) : null}
      {a.on && !a.overlay ? (
        <View style={{ marginTop: 12 }}>
          <Text style={st.sub}>{a.logs ? "" : "2. "}Allow WinLink to display over other apps, so it can read each copy for a moment while you're in another app.</Text>
          <View style={{ marginTop: 8, alignItems: "flex-start" }}>
            <Button st={st} title="Allow" kind="primary" onPress={() => controller.openOverlaySettings()} />
          </View>
        </View>
      ) : null}
      {a.on && ready ? (
        <Text style={[st.sub, { marginTop: 10, color: a.running ? c.text2 : c.warn }]}>
          {a.running ? "On. Copies go to your connected computers as you make them." : "Waiting to start: open WinLink again."}
        </Text>
      ) : null}
      {a.on ? (
        <Text style={[st.hint, { marginTop: 10 }]}>
          The log permission lets WinLink read Android's system log, not just the clipboard. WinLink only looks for the line Android writes when something is copied, and sends nothing else. Turn this off or uninstall WinLink to take it back.
        </Text>
      ) : null}
    </View>
  );
}

function ClipboardPage({ c, st, snap }: PageProps) {
  return (
    <View>
      <Text style={st.h1}>Clipboard</Text>
      <Text style={st.lead}>What you copy on a computer is ready to paste here{Platform.OS === "ios" ? " (while WinLink is open)" : ""}. To send this phone's clipboard, tap below.</Text>
      <View style={st.card}>
        <View style={st.row}>
          <View style={{ flex: 1 }}>
            <Text style={st.name}>Share the clipboard</Text>
            <Text style={st.sub}>Text and images, with every connected computer.</Text>
          </View>
          <Switch value={snap.clipboard.enabled} onValueChange={(v) => controller.setClipboard(v)} trackColor={{ true: c.accent }} />
        </View>
        <View style={{ marginTop: 12, alignItems: "flex-start" }}>
          <Button st={st} title="Send clipboard now" kind="primary" onPress={() => void controller.sendClipboard()} />
        </View>
        <Text style={[st.hint, { marginTop: 10 }]}>
          {Platform.OS === "android"
            ? "Faster: add the “Send clipboard” tile to Quick Settings, or use the button on WinLink's notification."
            : "Faster: make a Shortcut that opens winlink://send-clipboard, then set it on Settings › Accessibility › Touch › Back Tap. Double-tap the back of the phone after copying."}
        </Text>
      </View>
      {snap.autoClip ? <AutoClipCard c={c} st={st} a={snap.autoClip} /> : null}
      <Section st={st} title="History" right={snap.clipboard.history.length ? <Button st={st} title="Clear" kind="quiet" onPress={() => controller.clearClipHistory()} /> : undefined}>
        <View style={st.card}>
          {snap.clipboard.history.length ? snap.clipboard.history.map((h: ClipItem, i) => (
            <View key={h.id} style={[st.listRow, i > 0 && st.sep]}>
              <View style={{ flex: 1 }}>
                <Text style={st.body} numberOfLines={2}>{h.text}</Text>
                <Text style={st.sub}>{h.dir === "in" ? "from" : "to"} {h.peer} · {ago(h.at)}</Text>
              </View>
              {h.text !== "Image" ? <Button st={st} title="Copy" kind="quiet" onPress={() => void controller.copyAgain(h)} /> : null}
            </View>
          )) : <Text style={st.sub}>Nothing yet.</Text>}
        </View>
      </Section>
    </View>
  );
}

// ---------------------------------------------------------------- sound
function Sound({ c, st, snap }: PageProps) {
  const s = snap.sound;
  const [level, setLevel] = useState(0);
  useEffect(() => {
    if (!s.playingFrom) return;
    const t = setInterval(() => setLevel(controller.level()), 120);
    return () => clearInterval(t);
  }, [s.playingFrom]);
  const opts = targetOptions(snap).filter(([id]) => id !== "all");
  return (
    <View>
      <Text style={st.h1}>Sound</Text>
      <Text style={st.lead}>Play a computer's sound on this phone{s.canShare ? ", or this phone's sound on a computer" : ""}.</Text>
      <Section st={st} title="Playing here">
        <View style={st.card}>
          <View style={st.row}>
            <View style={{ flex: 1 }}>
              <Text style={st.name}>{s.playingFrom ? `Playing from ${s.playingFrom}` : "Nothing playing"}</Text>
              <Text style={st.sub}>{s.playingFrom ? s.playingInfo : "On a Mac or PC, turn on Share sound and choose this phone."}</Text>
            </View>
            {s.playingFrom ? <Button st={st} title="Stop" onPress={() => controller.stopPlayingHere()} /> : null}
          </View>
          {s.playingFrom ? <View style={[st.bar, { height: 6 }]}><View style={[st.barFill, { width: `${Math.round(Math.min(1, level * 1.4) * 100)}%`, backgroundColor: c.good }]} /></View> : null}
          <Text style={[st.label, { marginTop: 14 }]}>Latency</Text>
          <Chips st={st} options={[["auto", "Auto"], ["lowest", "Lowest"], ["low", "Low"], ["balanced", "Balanced"], ["smooth", "Smooth"]]}
            value={s.latency} onChange={(v) => controller.setLatency(v)} />
          <Text style={st.hint}>{({ auto: "As low as this network allows: starts around 40 ms on Wi‑Fi and only adds delay if sound breaks up.",
            lowest: "About 20 ms, for videos and games on good Wi‑Fi. May crackle on a busy network.",
            low: "About 50 ms. Lip-sync stays right for video.", balanced: "About 120 ms. A safe middle ground.",
            smooth: "About 300 ms. Never crackles; good for music." } as Record<string, string>)[s.latency]} Applies to the next stream.</Text>
          <Text style={[st.label, { marginTop: 14 }]}>Volume</Text>
          <Chips st={st} options={[["0.25", "25%"], ["0.5", "50%"], ["0.75", "75%"], ["1", "100%"]]}
            value={String(s.volume)} onChange={(v) => { controller.setVolume(Number(v)); }} />
        </View>
      </Section>
      {s.canShare ? (
        <Section st={st} title="Share this phone's sound">
          <View style={st.card}>
            <View style={st.row}>
              <View style={{ flex: 1 }}>
                <Text style={st.name}>Share sound</Text>
                <Text style={[st.sub, s.shareTone === "good" && { color: c.good }, s.shareTone === "bad" && { color: c.bad }]}>
                  {s.shareText || "Play what this phone plays on a computer. Android asks once to allow it."}
                </Text>
              </View>
              <Switch value={s.sharing} onValueChange={(v) => controller.setSharing(v)} trackColor={{ true: c.accent }} />
            </View>
            {opts.length ? (
              <>
                <Text style={[st.label, { marginTop: 12 }]}>Play on</Text>
                <Chips st={st} options={opts} value={s.target} onChange={(v) => controller.setSoundTarget(v)} />
              </>
            ) : null}
            <Text style={st.hint}>Some apps (Netflix, banking apps) don't allow their sound to be shared.</Text>
          </View>
        </Section>
      ) : null}
    </View>
  );
}

// ---------------------------------------------------------------- settings
function Settings({ c, st, snap }: PageProps) {
  const [name, setName] = useState(snap.me.name);
  const [showLog, setShowLog] = useState(false);
  return (
    <View>
      <Text style={st.h1}>Settings</Text>
      <Section st={st} title="This phone">
        <View style={st.card}>
          <Text style={st.label}>Name on your computers</Text>
          <View style={[st.row, { gap: 8 }]}>
            <TextInput value={name} onChangeText={setName} style={st.input} maxLength={48} />
            <Button st={st} title="Save" disabled={!name.trim() || name === snap.me.name} onPress={() => controller.setName(name)} />
          </View>
        </View>
      </Section>
      {Platform.OS === "android" ? (
        <Section st={st} title="In the background">
          <View style={st.card}>
            <View style={st.row}>
              <View style={{ flex: 1 }}>
                <Text style={st.name}>Stay connected</Text>
                <Text style={st.sub}>Keeps receiving files, clipboard and sound with the app closed. Shows a notification while connected.</Text>
              </View>
              <Switch value={snap.keepAlive} onValueChange={(v) => controller.setKeepAlive(v)} trackColor={{ true: c.accent }} />
            </View>
          </View>
          {snap.background ? (
            <View style={st.card}>
              <View style={st.row}>
                <View style={{ flex: 1 }}>
                  <Text style={st.name}>{snap.background.unrestricted ? "Allowed to run in the background" : "Android may pause WinLink"}</Text>
                  <Text style={[st.sub, !snap.background.unrestricted && { color: c.warn }]}>
                    {snap.background.unrestricted
                      ? `${snap.background.running ? "Running now: " : ""}files, clipboard and sound keep arriving with the app closed and the screen off.`
                      : "To save battery, Android pauses apps soon after you leave them, so your computers lose this phone. Allow WinLink to run unrestricted."}
                  </Text>
                </View>
              </View>
              {!snap.background.unrestricted ? (
                <View style={[st.row, { marginTop: 10 }]}>
                  <Button st={st} title="Allow" kind="primary" onPress={() => controller.allowBackground()} />
                </View>
              ) : null}
            </View>
          ) : null}
        </Section>
      ) : (
        <Section st={st} title="In the background">
          <View style={st.card}>
            <Text style={st.sub}>iPhones pause apps soon after you leave them. WinLink reconnects as soon as you open it, and sound keeps playing in the background.</Text>
          </View>
        </Section>
      )}
      <Section st={st} title="Privacy">
        <View style={st.card}>
          <Text style={st.sub}>Devices pair with a 4-digit code, prove they're paired on every connection, and encrypt everything between them. Pairing keys are kept in the phone's secure storage.</Text>
        </View>
      </Section>
      <Section st={st} title="Activity" right={<Button st={st} title={showLog ? "Hide" : "Show"} kind="quiet" onPress={() => setShowLog(!showLog)} />}>
        {showLog ? (
          <View style={st.card}>
            {controller.logs().slice(-80).map((l, i) => (
              <Text key={i} style={[st.log, l.level === "warn" && { color: c.warn }, l.level === "error" && { color: c.bad }]}>
                {new Date(l.t).toLocaleTimeString([], { hour12: false })} {l.msg}
              </Text>
            ))}
          </View>
        ) : null}
      </Section>
    </View>
  );
}

// ---------------------------------------------------------------- "Share > WinLink"
function ShareSheet({ c, st, snap }: PageProps) {
  const { shareIntent, resetShareIntent } = useShareIntentContext();
  const opts = targetOptions(snap);
  const [target, setTarget] = useState<string | null>(null);
  const chosen = opts.find(([id]) => id === target)?.[0] ?? opts[0]?.[0] ?? null;
  const files = shareIntent.files ?? [];
  const text = shareIntent.text || shareIntent.webUrl || "";
  return (
    <View style={st.sheetWrap}>
      <View style={st.sheet}>
        <Text style={st.h2}>{files.length ? `Send ${files.length === 1 ? files[0].fileName : `${files.length} files`}` : "Send to your computer"}</Text>
        {!files.length && text ? <Text style={[st.body, { marginTop: 6 }]} numberOfLines={3}>{text}</Text> : null}
        {opts.length ? (
          <>
            <Text style={[st.label, { marginTop: 12 }]}>To</Text>
            <Chips st={st} options={opts} value={chosen} onChange={setTarget} />
          </>
        ) : <Text style={[st.sub, { marginTop: 10 }]}>Waiting for a computer to connect…</Text>}
        <View style={[st.row, { gap: 8, marginTop: 14, justifyContent: "flex-end" }]}>
          <Button st={st} title="Cancel" kind="quiet" onPress={() => resetShareIntent()} />
          {files.length ? (
            <Button st={st} title="Send" kind="primary" disabled={!chosen} onPress={() => {
              controller.sendFiles(chosen!, files.map((f) => ({ uri: f.path, name: f.fileName, size: f.size })));
              resetShareIntent();
            }} />
          ) : (
            <Button st={st} title="Send as clipboard" kind="primary" disabled={!opts.length || !text} onPress={() => {
              controller.shareClip({ kind: "text", text });
              resetShareIntent();
            }} />
          )}
        </View>
      </View>
    </View>
  );
}

// ---------------------------------------------------------------- styles
function styles(c: Colors) {
  return StyleSheet.create({
    safe: { flex: 1, backgroundColor: c.bg },
    header: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 6 },
    brand: { fontSize: 15, fontWeight: "700", color: c.accent },
    headerSub: { fontSize: 12.5, color: c.text2, marginTop: 2 },
    page: { padding: 20, paddingBottom: 40 },
    h1: { fontSize: 26, fontWeight: "700", color: c.text, letterSpacing: -0.3 },
    lead: { fontSize: 14, color: c.text2, marginTop: 4, marginBottom: 14, lineHeight: 20 },
    h2: { fontSize: 16, fontWeight: "600", color: c.text },
    sectionHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between", marginBottom: 8 },
    card: { backgroundColor: c.card, borderRadius: 14, padding: 14, marginBottom: 10, borderWidth: StyleSheet.hairlineWidth, borderColor: c.line },
    ask: { backgroundColor: c.accentSoft, borderColor: c.accent },
    empty: { alignItems: "center", paddingVertical: 28 },
    row: { flexDirection: "row", alignItems: "center" },
    listRow: { flexDirection: "row", alignItems: "center", paddingVertical: 10, gap: 6 },
    sep: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: c.line },
    name: { fontSize: 15, fontWeight: "600", color: c.text },
    body: { fontSize: 14.5, color: c.text },
    sub: { fontSize: 13, color: c.text2, marginTop: 2, lineHeight: 18 },
    hint: { fontSize: 12.5, color: c.text3, marginTop: 6, lineHeight: 17 },
    label: { fontSize: 12.5, fontWeight: "600", color: c.text2, marginBottom: 6 },
    code: { fontSize: 34, fontWeight: "700", letterSpacing: 8, color: c.text, marginTop: 10, fontVariant: ["tabular-nums"] },
    btn: { paddingHorizontal: 14, height: 36, borderRadius: 10, backgroundColor: c.raised, alignItems: "center", justifyContent: "center" },
    btnPrimary: { backgroundColor: c.accent },
    btnQuiet: { backgroundColor: "transparent", paddingHorizontal: 10 },
    btnText: { fontSize: 14, fontWeight: "600", color: c.text },
    btnPrimaryText: { color: c.onAccent },
    btnQuietText: { color: c.text2 },
    btnDangerText: { color: c.bad },
    chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
    chip: { paddingHorizontal: 12, height: 32, borderRadius: 16, backgroundColor: c.raised, justifyContent: "center", maxWidth: 220 },
    chipOn: { backgroundColor: c.accentSoft, borderWidth: 1, borderColor: c.accent },
    chipText: { fontSize: 13.5, color: c.text2, fontWeight: "500" },
    chipTextOn: { color: c.accent, fontWeight: "700" },
    input: { flex: 1, height: 40, borderRadius: 10, backgroundColor: c.raised, paddingHorizontal: 12, color: c.text, fontSize: 15, marginRight: 8 },
    bar: { height: 4, borderRadius: 2, backgroundColor: c.track, marginTop: 8, overflow: "hidden" },
    barFill: { height: "100%", backgroundColor: c.accent, borderRadius: 2 },
    tabs: { flexDirection: "row", borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: c.line, backgroundColor: c.card,
      paddingBottom: Platform.OS === "android" ? 10 : 0, paddingTop: 6 },
    tab: { flex: 1, alignItems: "center" },
    tabPill: { width: 56, height: 30, borderRadius: 15, alignItems: "center", justifyContent: "center" },
    tabIcon: { width: 22, height: 22 },
    tabBadge: { position: "absolute", top: -3, right: 6 },
    tabPillOn: { backgroundColor: c.accentSoft },
    tabText: { fontSize: 11, color: c.text2, fontWeight: "500", marginTop: 3 },
    tabTextOn: { color: c.accent, fontWeight: "700" },
    badge: { minWidth: 16, height: 16, borderRadius: 8, backgroundColor: c.accent, alignItems: "center", justifyContent: "center", paddingHorizontal: 4 },
    badgeText: { fontSize: 10, color: c.onAccent, fontWeight: "700" },
    toast: { position: "absolute", left: 16, right: 16, bottom: 80, zIndex: 10, backgroundColor: c.text, borderRadius: 12, padding: 12 },
    toastText: { color: c.bg, fontSize: 13.5 },
    sheetWrap: { position: "absolute", left: 0, right: 0, top: 0, bottom: 0, zIndex: 20, backgroundColor: "rgba(0,0,0,0.35)", justifyContent: "flex-end" },
    sheet: { backgroundColor: c.card, borderTopLeftRadius: 18, borderTopRightRadius: 18, padding: 20, paddingBottom: 36 },
    log: { fontSize: 11.5, color: c.text2, fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace", marginBottom: 2 },
  });
}
