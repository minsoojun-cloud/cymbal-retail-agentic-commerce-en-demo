/* Cymbal Retail store app — voice input (window.Voice).
   Voice.attach(inputEl, micBtnEl, onFinal) -> {stop}. Web Speech API (en-US) with live interim text in inputEl;
   fallback: MediaRecorder -> POST /api/voice/transcribe (Gemini). Tap the mic again to stop. */
(function () {
  'use strict';
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const MAX_MS = 12000;
  let active = null;  // the session currently listening (only one at a time)

  function toast(m) { if (window.SN && window.SN.toast) window.SN.toast(m); }
  function supported() { return !!(SR || (navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder)); }

  function setUI(btn, on) {
    if (!btn) return;
    btn.classList.toggle('is-listening', on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.setAttribute('aria-label', on ? 'Stop listening' : 'Speak');
    btn.title = on ? 'Stop listening' : 'Speak';
  }

  function speech(input, btn, onFinal) {
    const rec = new SR();
    rec.lang = 'en-US'; rec.interimResults = true; rec.continuous = false; rec.maxAlternatives = 1;
    let finalText = '', done = false;
    const base = input ? input.value : '';
    const sess = { stop() { try { rec.stop(); } catch (e) { /* ignore */ } } };
    rec.onresult = (e) => {
      let interim = '';
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const t = e.results[i][0].transcript;
        if (e.results[i].isFinal) finalText += t; else interim += t;
      }
      if (input) { input.value = (finalText + interim).trim() || base; input.dispatchEvent(new Event('input', { bubbles: true })); }
    };
    rec.onerror = (e) => {
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed') toast('Microphone access is blocked');
      else if (e.error === 'no-speech') toast('Didn\u2019t catch that \u2014 please try again');
      else if (e.error === 'network' && window.MediaRecorder) { done = true; finish(); start(input, btn, onFinal, true); }
    };
    rec.onend = () => finish();
    function finish() {
      if (active === sess) active = null;
      if (!active) setUI(btn, false);
      if (done) return;
      done = true;
      const t = finalText.trim();
      if (t && onFinal) onFinal(t);
    }
    rec.start();
    return sess;
  }

  function recorder(input, btn, onFinal) {
    const sess = { stopped: false, stop() { this.stopped = true; if (this.mr && this.mr.state !== 'inactive') this.mr.stop(); } };
    const ph = input ? input.placeholder : '';
    navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
      if (sess.stopped) { stream.getTracks().forEach((t) => t.stop()); setUI(btn, false); return; }
      const types = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];
      const mime = types.find((t) => window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(t)) || '';
      const mr = new window.MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      const chunks = [];
      sess.mr = mr;
      if (input) input.placeholder = 'Listening\u2026 tap the mic to finish';
      mr.ondataavailable = (e) => { if (e.data && e.data.size) chunks.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        clearTimeout(sess.timer);
        if (active === sess) active = null;
        setUI(btn, false);
        if (input) input.placeholder = 'Transcribing\u2026';
        try {
          const type = (mr.mimeType || mime || 'audio/webm').split(';')[0];
          const ext = type.indexOf('mp4') >= 0 ? 'mp4' : type.indexOf('ogg') >= 0 ? 'ogg' : 'webm';
          const fd = new FormData();
          fd.append('audio', new Blob(chunks, { type }), 'speech.' + ext);
          const r = await fetch('/api/voice/transcribe', { method: 'POST', body: fd, credentials: 'same-origin' });
          const j = await r.json().catch(() => ({}));
          if (!r.ok) throw new Error(j.error || 'Voice input is unavailable');
          const t = String(j.text || '').trim();
          if (!t) { toast('Didn\u2019t catch that \u2014 please try again'); return; }
          if (input) { input.value = t; input.dispatchEvent(new Event('input', { bubbles: true })); }
          if (onFinal) onFinal(t);
        } catch (e) {
          toast(e.message || 'Voice input is unavailable');
        } finally {
          if (input) input.placeholder = ph;
        }
      };
      mr.start();
      sess.timer = setTimeout(() => sess.stop(), MAX_MS);
    }).catch(() => { if (active === sess) active = null; setUI(btn, false); toast('Microphone access is blocked'); });
    return sess;
  }

  function start(input, btn, onFinal, forceRecorder) {
    if (active) active.stop();
    setUI(btn, true);
    try {
      active = (SR && !forceRecorder) ? speech(input, btn, onFinal) : recorder(input, btn, onFinal);
    } catch (e) {
      setUI(btn, false); active = null; toast('Voice input is unavailable');
    }
    return active;
  }

  function attach(input, btn, onFinal) {
    if (!btn) return null;
    if (!supported()) { btn.hidden = true; return null; }
    if (btn._featVoice) return btn._featVoice;
    let mine = null;
    btn.setAttribute('aria-pressed', 'false');
    if (!btn.getAttribute('aria-label')) btn.setAttribute('aria-label', 'Speak');
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      if (mine && active === mine) { mine.stop(); return; }
      mine = start(input, btn, onFinal);
    });
    btn._featVoice = { stop() { if (mine) mine.stop(); } };
    return btn._featVoice;
  }

  window.Voice = { attach, supported };
})();
