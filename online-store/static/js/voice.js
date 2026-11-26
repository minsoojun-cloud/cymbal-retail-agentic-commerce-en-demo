/* Voice for the AI Concierge chat (CONTRACT v6). Active only when CR_CONFIG.voiceEnabled (env VOICE_ENABLED=1,
 * cymbal-site-adk); otherwise this file does nothing and the DOM is unchanged.
 *
 *  - Mic button in the chat input: tap to talk / tap to stop (also stops by itself after ~1.1 s of silence).
 *  - "Hands-free" toggle: continuous conversation — listens, detects the end of each utterance (silence), sends it,
 *    pauses while the agent answers / speaks, then listens again.
 *  - Recording indicator + level meter + live transcript (browser Web Speech API interim results when available).
 *  - Final text: Web Speech final result when the browser provides one, else POST /api/voice/stt (Speech-to-Text v2
 *    Chirp / Gemini). The transcript is sent to the agent automatically (send(text, {via:'voice'})).
 *  - Read-aloud: "Read aloud" speaker toggle next to Hands-free (chat header unchanged); reply text is spoken sentence by sentence while it streams
 *    (POST /api/voice/tts → en-US-Chirp3-HD-Aoede), limited to the first ~320 characters; stops on any new input.
 *  Query param ?voice_stt=server forces the server STT path (tests).
 */
(function () {
  'use strict';
  const CFG = window.CR_CONFIG || {};
  if (!CFG.voiceEnabled) return;
  const VCFG = Object.assign({ sttEndpoint: '/api/voice/stt', ttsEndpoint: '/api/voice/tts', lang: 'en-US' }, CFG.voice || {});
  const CR = window.CR;
  const log = (...a) => console.log('[CR-VOICE]', ...a);
  const qs = new URLSearchParams(location.search);
  const FORCE_SERVER = qs.get('voice_stt') === 'server';
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const LS_SPEAKER = 'cr_voice_speaker';
  const LS_HANDS = 'cr_voice_handsfree';
  const SPEAK_MAX = 320;            // chars read aloud per reply (the cards show the details)
  const SILENCE_MS = 1100;          // end of utterance
  const NO_SPEECH_MS = 8000;        // tap mode: give up when nothing was said
  const MAX_UTTER_MS = 20000;

  const ICON = {
    mic: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5.3-3a.75.75 0 0 0-1.5 0 3.8 3.8 0 0 1-7.6 0 .75.75 0 0 0-1.5 0 5.3 5.3 0 0 0 4.55 5.24V19H8.5a.75.75 0 0 0 0 1.5h7a.75.75 0 0 0 0-1.5h-2.75v-2.76A5.3 5.3 0 0 0 17.3 11z"/></svg>',
    stop: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2.5"/></svg>',
    spkOn: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10v4c0 .55.45 1 1 1h3l3.29 3.29c.63.63 1.71.18 1.71-.71V6.41c0-.89-1.08-1.34-1.71-.71L7 9H4c-.55 0-1 .45-1 1zm13.5 2A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 4.45v.2c0 .38.25.71.6.85C17.18 6.53 19 9.06 19 12s-1.82 5.47-4.4 6.5c-.36.14-.6.47-.6.85v.2c0 .63.63 1.07 1.21.85C18.6 19.11 21 15.84 21 12s-2.4-7.11-5.79-8.4c-.58-.23-1.21.22-1.21.85z"/></svg>',
    spkOff: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3.63 3.63a.996.996 0 0 0 0 1.41L7.29 8.7 7 9H4c-.55 0-1 .45-1 1v4c0 .55.45 1 1 1h3l3.29 3.29c.63.63 1.71.18 1.71-.71v-4.17l4.18 4.18c-.49.37-1.02.68-1.6.91-.36.15-.58.53-.58.92 0 .72.73 1.18 1.39.91.8-.33 1.55-.77 2.22-1.31l1.34 1.34a.996.996 0 1 0 1.41-1.41L5.05 3.63c-.39-.39-1.02-.39-1.42 0zM19 12c0 .82-.15 1.61-.41 2.34l1.53 1.53c.56-1.17.88-2.48.88-3.87 0-3.83-2.4-7.11-5.78-8.4-.59-.23-1.22.23-1.22.86v.19c0 .38.25.71.61.85C17.18 6.54 19 9.06 19 12zm-8.71-6.29-.17.17L12 7.76V6.41c0-.89-1.08-1.33-1.71-.7zM16.5 12A4.5 4.5 0 0 0 14 7.97v1.79l2.48 2.48c.01-.08.02-.16.02-.24z"/></svg>',
    hands: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zM4.5 9.5a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5a.75.75 0 0 1 .75-.75zm15 0a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5a.75.75 0 0 1 .75-.75zM12 17.5a.75.75 0 0 1 .75.75v1.5a.75.75 0 0 1-1.5 0v-1.5a.75.75 0 0 1 .75-.75z"/></svg>',
  };

  // ------------------------------------------------------------------ state
  const st = {
    speaker: readBool(LS_SPEAKER, true),
    handsFree: false,
    phase: 'idle',               // idle | listening | transcribing | waiting (hands-free: agent turn / speaking)
    stream: null, ctx: null, analyser: null, src: null, rec: null, chunks: [], recMime: '',
    vadTimer: null, startedAt: 0, heardAt: 0, lastVoiceAt: 0, speechDetected: false, noiseFloor: 0.01, calib: [],
    sr: null, srFinal: '', srInterim: '', srBroken: FORCE_SERVER || !SR, srEnded: true,
    seg: 0,                      // utterance generation (late results of an aborted utterance are ignored)
    permission: 'unknown',
    timings: [],
  };
  function readBool(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v === '1'; } catch (e) { return d; } }
  function writeBool(k, v) { try { localStorage.setItem(k, v ? '1' : '0'); } catch (e) { /* ignore */ } }

  let micBtn, handsBtn, spkBtn, liveEl, liveText, liveLabel, meterEl, form, ta;

  // ------------------------------------------------------------------ UI
  function buildUi() {
    form = document.getElementById('cr-chat-form');
    ta = document.getElementById('cr-chat-input');
    const send = document.getElementById('cr-chat-send');
    if (!form || !send) return false;
    document.getElementById('cr-chat').classList.add('has-voice');

    micBtn = document.createElement('button');
    micBtn.type = 'button'; micBtn.id = 'cr-voice-mic'; micBtn.className = 'cr-voice-mic';
    micBtn.setAttribute('aria-label', 'Voice input'); micBtn.title = 'Tap to talk (tap again to stop)';
    micBtn.innerHTML = ICON.mic;
    form.insertBefore(micBtn, send);

    // speaker toggle lives in the voice bar (next to Hands-free) so the chat header / subtitle stay unchanged
    spkBtn = document.createElement('button');
    spkBtn.type = 'button'; spkBtn.id = 'cr-voice-speaker'; spkBtn.className = 'cr-voice-spk';

    liveEl = document.createElement('div');
    liveEl.id = 'cr-voice-live'; liveEl.className = 'cr-voice-live'; liveEl.hidden = true;
    liveEl.setAttribute('role', 'status'); liveEl.setAttribute('aria-live', 'polite');
    liveEl.innerHTML = '<span class="cr-voice-live__dot" aria-hidden="true"></span>' +
      '<span class="cr-voice-meter" aria-hidden="true"><i></i><i></i><i></i><i></i><i></i></span>' +
      '<span class="cr-voice-live__body"><b class="cr-voice-live__label">Listening…</b><span class="cr-voice-live__text" id="cr-voice-transcript"></span></span>';
    liveText = liveEl.querySelector('.cr-voice-live__text');
    liveLabel = liveEl.querySelector('.cr-voice-live__label');
    meterEl = liveEl.querySelector('.cr-voice-meter');
    form.parentNode.insertBefore(liveEl, form);

    const bar = document.createElement('div');
    bar.className = 'cr-voice-bar';
    handsBtn = document.createElement('button');
    handsBtn.type = 'button'; handsBtn.id = 'cr-voice-handsfree'; handsBtn.className = 'cr-voice-hands';
    handsBtn.setAttribute('role', 'switch');
    handsBtn.innerHTML = ICON.hands + '<span>Hands-free</span><i class="cr-voice-hands__sw" aria-hidden="true"></i>';
    handsBtn.title = 'Keep the conversation going just by talking (sends automatically when you pause)';
    const hint = document.createElement('span');
    hint.className = 'cr-voice-hint'; hint.id = 'cr-voice-hint';
    hint.textContent = 'Tap the mic and start talking';
    bar.appendChild(handsBtn); bar.appendChild(spkBtn); bar.appendChild(hint);
    form.parentNode.insertBefore(bar, form.nextSibling);

    micBtn.addEventListener('click', onMicClick);
    handsBtn.addEventListener('click', () => setHandsFree(!st.handsFree));
    spkBtn.addEventListener('click', () => setSpeaker(!st.speaker));
    renderSpeaker();
    renderHands();
    if (!canRecord()) {
      micBtn.disabled = true;
      micBtn.title = window.isSecureContext ? "This browser doesn't support voice input" : 'Voice input is only available over HTTPS';
      handsBtn.disabled = true;
      setHint(micBtn.title);
    }
    return true;
  }
  function canRecord() { return !!(window.isSecureContext && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder); }
  function setHint(t) { const h = document.getElementById('cr-voice-hint'); if (h) h.textContent = t; }
  function renderSpeaker() {
    spkBtn.innerHTML = (st.speaker ? ICON.spkOn : ICON.spkOff) + '<span>Read aloud</span>';
    spkBtn.setAttribute('aria-pressed', String(st.speaker));
    const t = st.speaker ? 'Read aloud: on (tap to turn off)' : 'Read aloud: off (tap to turn on)';
    spkBtn.title = t; spkBtn.setAttribute('aria-label', t);
    spkBtn.classList.toggle('is-on', st.speaker);
  }
  function renderHands() {
    handsBtn.setAttribute('aria-checked', String(st.handsFree));
    handsBtn.classList.toggle('is-on', st.handsFree);
  }
  function setPhase(p) {
    st.phase = p;
    const panel = document.getElementById('cr-chat');
    ['listening', 'transcribing', 'waiting'].forEach((x) => panel.classList.toggle('voice-' + x, p === x));
    form.classList.toggle('is-recording', p === 'listening');
    micBtn.classList.toggle('is-recording', p === 'listening');
    micBtn.classList.toggle('is-busy', p === 'transcribing');
    micBtn.innerHTML = p === 'listening' ? ICON.stop : ICON.mic;
    micBtn.setAttribute('aria-label', p === 'listening' ? 'Stop recording and send' : 'Voice input');
    micBtn.setAttribute('aria-pressed', String(p === 'listening'));
    liveEl.hidden = !(p === 'listening' || p === 'transcribing' || (p === 'waiting' && st.handsFree));
    liveEl.dataset.phase = p;
    if (p === 'listening') { liveLabel.textContent = st.handsFree ? 'Hands-free: listening…' : 'Listening…'; }
    else if (p === 'transcribing') liveLabel.textContent = 'Transcribing…';
    else if (p === 'waiting') { liveLabel.textContent = 'Hands-free: waiting for a reply…'; liveText.textContent = ''; }
    setHint(st.handsFree ? (p === 'listening' ? "I'll send it automatically when you finish speaking" : 'Hands-free conversation on') : (p === 'listening' ? 'Tap again when you are done (or just pause to send)' : 'Tap the mic and start talking'));
  }

  // ------------------------------------------------------------------ speaker (TTS)
  const tts = { gen: 0, queue: [], playing: null, running: false, spokenUpTo: 0, spokenChars: 0, stoppedTurn: false, cache: new Map(), unlocked: false };
  function setSpeaker(on) {
    st.speaker = !!on; writeBool(LS_SPEAKER, st.speaker); renderSpeaker();
    if (!st.speaker) stopSpeaking();
  }
  function stopSpeaking() {
    tts.gen++;
    tts.queue.forEach((q) => q.abort && q.abort());
    tts.queue = [];
    tts.running = false;
    const pl = tts.playing; tts.playing = null;
    if (pl) { try { pl.pause(); pl.removeAttribute('src'); } catch (e) { /* ignore */ } }
    document.getElementById('cr-chat').classList.remove('voice-speaking');
  }
  function fetchTts(text) {
    if (tts.cache.has(text)) return { promise: Promise.resolve(tts.cache.get(text)) };
    const ac = new AbortController();
    const t0 = performance.now();
    const promise = fetch(VCFG.ttsEndpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }), signal: ac.signal })
      .then((r) => { if (!r.ok) throw new Error('tts ' + r.status); return r.blob(); })
      .then((b) => { const u = URL.createObjectURL(b); tts.cache.set(text, u); st.timings.push({ kind: 'tts', ms: Math.round(performance.now() - t0), chars: text.length }); return u; });
    return { promise, abort: () => ac.abort() };
  }
  function enqueueSpeech(text) {
    text = String(text || '').trim();
    if (!text || !st.speaker) return;
    const item = Object.assign({ text }, fetchTts(text));
    item.promise.catch((e) => { if (e.name !== 'AbortError') console.warn('[CR-VOICE] tts failed', e); });
    tts.queue.push(item);
    if (!tts.running) runQueue();
  }
  /** one playback loop at a time (tts.running) — pending fetches count as "speaking" so hands-free does not resume early */
  async function runQueue() {
    const gen = tts.gen;
    tts.running = true;
    const panel = document.getElementById('cr-chat');
    try {
      while (gen === tts.gen && tts.queue.length) {
        const item = tts.queue.shift();
        let url;
        try { url = await item.promise; } catch (e) { continue; }
        if (gen !== tts.gen) break;
        const a = new Audio(url);
        tts.playing = a;
        if (turnT.t0 && !turnT.audio) { turnT.audio = Math.round(performance.now() - turnT.t0); st.timings.push({ kind: 'turn_first_audio', ms: turnT.audio, via: turnT.via }); }
        panel.classList.add('voice-speaking');
        const done = new Promise((res) => { a.onended = a.onerror = a.onpause = () => res(); });
        try { await a.play(); } catch (e) { log('audio play failed', e.name); tts.queue = []; break; }
        await done;
      }
    } finally {
      if (gen === tts.gen) {
        tts.running = false; tts.playing = null;
        panel.classList.remove('voice-speaking');
        onSpeechIdle();
      }
    }
  }
  function isSpeaking() { return tts.running || !!tts.playing || tts.queue.length > 0; }
  /** sentence-chunked read-aloud of the streaming reply (first SPEAK_MAX chars) */
  function onBotDelta(full, final) {
    if (!st.speaker || tts.stoppedTurn) return;
    const rest = full.slice(tts.spokenUpTo);
    let cut = -1;
    const re = /[.!?]+(?=\s)|\n+/g; // sentence end = punctuation followed by whitespace ('4.5', '¥1,280.' mid-stream are not cut)
    let m;
    while ((m = re.exec(rest))) cut = m.index + m[0].length;
    if (final && rest.trim()) cut = rest.length;
    if (cut <= 0) return;
    const chunk = rest.slice(0, cut);
    tts.spokenUpTo += cut;
    // split into sentences, speak until the budget is used
    const sentences = chunk.split(/(?<=[.!?])\s+|\n+/).map((x) => x.trim()).filter((x) => x.replace(/[\s.!?*#>-]/g, ''));
    let buf = '';
    for (const sn of sentences) {
      if (tts.spokenChars >= SPEAK_MAX) { tts.stoppedTurn = true; break; }
      if (/^\s*(?:[-*•]|\d+[.)]|\|)/.test(sn) && tts.spokenChars > 0) { tts.stoppedTurn = true; break; } // lists/tables: the cards show them
      tts.spokenChars += sn.length;
      buf += (buf ? ' ' : '') + sn;
      if (buf.length > 40) { enqueueSpeech(buf); buf = ''; }
    }
    if (buf) enqueueSpeech(buf);
  }

  // ------------------------------------------------------------------ recording
  async function ensureStream() {
    if (st.stream && st.stream.active) return st.stream;
    try {
      st.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
      st.permission = 'granted';
    } catch (e) {
      st.permission = e && (e.name === 'NotAllowedError' || e.name === 'SecurityError') ? 'denied' : 'error';
      onMicError(e);
      throw e;
    }
    const Ctx = window.AudioContext || window.webkitAudioContext;
    st.ctx = st.ctx || new Ctx();
    if (st.ctx.state === 'suspended') { try { await st.ctx.resume(); } catch (e) { /* ignore */ } }
    st.src = st.ctx.createMediaStreamSource(st.stream);
    st.analyser = st.ctx.createAnalyser();
    st.analyser.fftSize = 1024;
    st.src.connect(st.analyser);
    return st.stream;
  }
  function releaseStream() {
    if (st.stream) st.stream.getTracks().forEach((t) => t.stop());
    st.stream = null;
    try { st.src && st.src.disconnect(); } catch (e) { /* ignore */ }
    st.src = null; st.analyser = null;
  }
  function onMicError(e) {
    console.warn('[CR-VOICE] mic error', e);
    const denied = st.permission === 'denied';
    const msg = denied ? 'Microphone access is blocked. Allow the microphone in your address bar settings to talk to the concierge.'
      : e && e.name === 'NotFoundError' ? 'No microphone found. Please check the connection.' : "Couldn't start the microphone.";
    CR.toast(msg, { ms: 5000 });
    micBtn.classList.add('is-denied');
    if (st.handsFree) setHandsFree(false, { silent: true });
    setPhase('idle');
    setHint(msg);
    micBtn.title = msg;
  }
  function pickMime() {
    const c = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
    return c.find((m) => window.MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) || '';
  }

  async function startListening() {
    if (st.phase === 'listening' || st.phase === 'transcribing') return;
    stopSpeaking();
    try { await ensureStream(); } catch (e) { return; }
    micBtn.classList.remove('is-denied');
    const seg = ++st.seg;
    st.chunks = []; st.recMime = pickMime();
    try { st.rec = new MediaRecorder(st.stream, st.recMime ? { mimeType: st.recMime, audioBitsPerSecond: 32000 } : undefined); }
    catch (e) { st.rec = new MediaRecorder(st.stream); st.recMime = st.rec.mimeType; }
    st.rec.ondataavailable = (ev) => { if (ev.data && ev.data.size && seg === st.seg) st.chunks.push(ev.data); };
    st.rec.start(250);
    st.startedAt = performance.now(); st.heardAt = 0; st.lastVoiceAt = 0; st.speechDetected = false; st.calib = [];
    st.srFinal = ''; st.srInterim = '';
    liveText.textContent = '';
    setPhase('listening');
    startWebSpeech(seg);
    vadLoop(seg);
    log('listening', { seg, mime: st.recMime, handsFree: st.handsFree, webSpeech: !st.srBroken });
  }

  function rms() {
    if (!st.analyser) return 0;
    const buf = new Float32Array(st.analyser.fftSize);
    st.analyser.getFloatTimeDomainData(buf);
    let s = 0;
    for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    return Math.sqrt(s / buf.length);
  }
  function vadLoop(seg) {
    clearInterval(st.vadTimer);
    st.vadTimer = setInterval(() => {
      if (seg !== st.seg || st.phase !== 'listening') { clearInterval(st.vadTimer); return; }
      const now = performance.now();
      const level = rms();
      const el = now - st.startedAt;
      if (el < 350) { st.calib.push(level); st.noiseFloor = Math.max(0.004, Math.min(0.03, st.calib.reduce((a, b) => a + b, 0) / st.calib.length)); }
      const thr = Math.max(0.018, st.noiseFloor * 2.8);
      const voiced = level > thr;
      if (voiced) { st.lastVoiceAt = now; if (!st.speechDetected && el >= 120) { st.speechDetected = true; st.heardAt = now; } }
      // meter
      const lv = Math.min(1, level / 0.12);
      Array.from(meterEl.children).forEach((b, i) => { b.style.transform = 'scaleY(' + Math.max(0.18, Math.min(1, lv * (1.25 - Math.abs(i - 2) * 0.22))).toFixed(2) + ')'; });
      if (st.speechDetected && now - st.lastVoiceAt > SILENCE_MS) { log('silence → stop'); stopListening(true); return; }
      if (st.speechDetected && now - st.heardAt > MAX_UTTER_MS) { stopListening(true); return; }
      if (!st.speechDetected && el > NO_SPEECH_MS) {
        if (st.handsFree) { restartSegment(); return; }
        log('no speech'); stopListening(false); setHint("Sorry, I couldn't hear you. Tap the mic and try again.");
      }
    }, 50);
  }
  /** hands-free: nothing said for a while → drop the recorded silence and keep listening */
  function restartSegment() {
    abortSegment();
    setTimeout(() => { if (st.handsFree && st.phase === 'idle') startListening(); }, 30);
  }
  function abortSegment() {
    st.seg++;
    clearInterval(st.vadTimer);
    stopWebSpeech(true);
    try { if (st.rec && st.rec.state !== 'inactive') st.rec.stop(); } catch (e) { /* ignore */ }
    st.rec = null; st.chunks = [];
    setPhase('idle');
  }

  function startWebSpeech(seg) {
    if (st.srBroken) return;
    try {
      const r = new SR();
      r.lang = VCFG.lang || 'en-US'; r.interimResults = true; r.continuous = true; r.maxAlternatives = 1;
      r.onresult = (ev) => {
        if (seg !== st.seg) return;
        let fin = '', interim = '';
        for (let i = 0; i < ev.results.length; i++) {
          const t = ev.results[i][0].transcript;
          if (ev.results[i].isFinal) fin += t; else interim += t;
        }
        st.srFinal = fin; st.srInterim = interim;
        liveText.textContent = fin + interim;
      };
      r.onerror = (ev) => {
        log('web speech error', ev.error);
        if (['network', 'not-allowed', 'service-not-allowed', 'language-not-supported', 'audio-capture'].includes(ev.error)) st.srBroken = true;
      };
      r.onend = () => { st.srEnded = true; };
      st.sr = r; st.srEnded = false;
      r.start();
    } catch (e) { log('web speech unavailable', e); st.srBroken = true; st.sr = null; }
  }
  function stopWebSpeech(abort) {
    const r = st.sr; st.sr = null;
    if (!r) return;
    try { abort ? r.abort() : r.stop(); } catch (e) { /* ignore */ }
  }
  function waitFor(cond, ms) {
    return new Promise((res) => { const t0 = performance.now(); const id = setInterval(() => { if (cond() || performance.now() - t0 > ms) { clearInterval(id); res(); } }, 40); });
  }

  /** stop recording; submit=true → transcribe + send */
  async function stopListening(submit) {
    if (st.phase !== 'listening') return;
    const seg = st.seg;
    clearInterval(st.vadTimer);
    const rec = st.rec; st.rec = null;
    const heard = st.speechDetected;
    const tStop = performance.now();
    const stopped = new Promise((res) => { if (!rec || rec.state === 'inactive') res(); else { rec.onstop = () => res(); } });
    try { if (rec && rec.state !== 'inactive') rec.stop(); } catch (e) { /* ignore */ }
    const hadSr = !!st.sr;
    stopWebSpeech(false);
    if (!submit || !heard) {
      st.seg++;
      setPhase('idle');
      if (submit && !heard) setHint("Sorry, I couldn't hear you. Please try again.");
      if (st.handsFree) restartSegment(); else if (!st.handsFree) releaseStream();
      return;
    }
    setPhase('transcribing');
    await stopped;
    if (hadSr && !st.srBroken) await waitFor(() => st.srEnded || st.srBroken, 700);
    if (seg !== st.seg) return;
    let text = (st.srFinal || '').trim();
    let engine = 'webspeech';
    if (!text) {
      const blob = new Blob(st.chunks, { type: st.recMime || 'audio/webm' });
      st.chunks = [];
      liveText.textContent = liveText.textContent || '…';
      try {
        const t0 = performance.now();
        const r = await fetch(VCFG.sttEndpoint, { method: 'POST', headers: { 'Content-Type': blob.type || 'audio/webm' }, body: blob });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
        text = String(j.text || '').trim();
        engine = j.engine || 'server';
        st.timings.push({ kind: 'stt', ms: Math.round(performance.now() - t0), server_ms: j.ms, engine, bytes: blob.size });
      } catch (e) {
        console.warn('[CR-VOICE] stt failed', e);
        if (seg === st.seg) { setPhase('idle'); setHint("Sorry, I couldn't transcribe that. Please try again."); CR.toast("Couldn't transcribe your voice"); if (st.handsFree) restartSegment(); }
        return;
      }
    }
    if (seg !== st.seg) return;
    const stopToText = Math.round(performance.now() - tStop);
    st.timings.push({ kind: 'utterance', engine, stop_to_text_ms: stopToText, text });
    log('transcript', engine, stopToText + 'ms', text);
    if (!text) {
      setPhase('idle'); setHint("Sorry, I couldn't hear you. Please try again.");
      if (st.handsFree) restartSegment(); else releaseStream();
      return;
    }
    liveText.textContent = text;
    if (st.handsFree) setPhase('waiting'); else { setPhase('idle'); releaseStream(); }
    window.CRChat.send(text, { via: 'voice' });
  }

  function onMicClick() {
    unlockAudio();
    if (st.phase === 'listening') { stopListening(true); return; }
    if (st.phase === 'transcribing') return;
    if (st.phase === 'waiting') {
      // barge-in: stop the read-aloud and listen now (if the agent is not still answering)
      stopSpeaking();
      if (!(window.CRChat.isBusy && window.CRChat.isBusy())) { setPhase('idle'); startListening(); }
      return;
    }
    startListening();
  }
  function setHandsFree(on, opts) {
    st.handsFree = !!on; writeBool(LS_HANDS, st.handsFree); renderHands();
    unlockAudio();
    if (st.handsFree) {
      if (!(opts && opts.silent)) CR.toast('Hands-free conversation is on. Go ahead and talk.');
      if (st.phase === 'idle' && !(window.CRChat.isBusy && window.CRChat.isBusy())) startListening();
      else if (st.phase === 'idle') setPhase('waiting');
    } else {
      if (st.phase === 'listening' || st.phase === 'waiting') abortSegment();
      releaseStream();
      setPhase('idle');
    }
  }
  /** hands-free: resume listening once the agent turn is over and the reply has been read */
  function onSpeechIdle() {
    if (!st.handsFree || st.phase !== 'waiting') return;
    if (window.CRChat.isBusy && window.CRChat.isBusy()) return;
    if (isSpeaking()) return;
    setTimeout(() => { if (st.handsFree && st.phase === 'waiting' && !isSpeaking() && !(window.CRChat.isBusy && window.CRChat.isBusy())) { setPhase('idle'); startListening(); } }, 350);
  }
  /** a user gesture lets later audio.play() calls start without a gesture (autoplay policy) */
  function unlockAudio() {
    if (tts.unlocked) return;
    tts.unlocked = true;
    try { const a = new Audio('data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA='); a.play().catch(() => {}); } catch (e) { /* ignore */ }
  }

  // ------------------------------------------------------------------ chat hooks (chat.js events)
  const turnT = { t0: 0, text: 0, audio: 0, via: '' };
  window.addEventListener('cr-chat-user-send', (ev) => {
    Object.assign(turnT, { t0: performance.now(), text: 0, audio: 0, via: (ev.detail && ev.detail.via) || '' });
    stopSpeaking();
    tts.spokenUpTo = 0; tts.spokenChars = 0; tts.stoppedTurn = false;
    unlockAudio();
    // typed input while listening → drop the recording
    if (ev.detail && ev.detail.via !== 'voice' && st.phase === 'listening') abortSegment();
    if (st.handsFree && st.phase === 'idle') setPhase('waiting');
  });
  window.addEventListener('cr-chat-bot-delta', (ev) => {
    if (turnT.t0 && !turnT.text) { turnT.text = Math.round(performance.now() - turnT.t0); st.timings.push({ kind: 'turn_first_text', ms: turnT.text, via: turnT.via }); }
    onBotDelta((ev.detail && ev.detail.text) || '', false);
  });
  window.addEventListener('cr-chat-turn-end', (ev) => {
    if (turnT.t0) st.timings.push({ kind: 'turn_end', ms: Math.round(performance.now() - turnT.t0), via: turnT.via });
    onBotDelta((ev.detail && ev.detail.text) || '', true);
    if (!isSpeaking()) onSpeechIdle();
  });
  window.addEventListener('cr-chat-reset', () => { stopSpeaking(); if (st.phase !== 'idle') abortSegment(); if (st.handsFree) setHandsFree(false, { silent: true }); });
  document.addEventListener('click', (e) => {
    if (e.target.closest && e.target.closest('#cr-chat-close') && st.handsFree) setHandsFree(false, { silent: true });
  });
  document.addEventListener('visibilitychange', () => { if (document.hidden && st.phase === 'listening' && !st.handsFree) stopListening(false); });

  window.CRVoice = {
    state: () => ({ phase: st.phase, handsFree: st.handsFree, speaker: st.speaker, speaking: isSpeaking(), permission: st.permission, webSpeech: !st.srBroken }),
    timings: () => st.timings.slice(),
    start: startListening, stop: () => stopListening(true), setHandsFree, setSpeaker, stopSpeaking, speak: enqueueSpeech,
  };

  function boot() {
    if (!buildUi()) return;
    log('voice enabled', { webSpeech: !st.srBroken, forceServer: FORCE_SERVER, secure: window.isSecureContext });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
