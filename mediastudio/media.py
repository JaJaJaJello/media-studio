"""Media decoding: video (PyAV, threaded with frame cache), animations (Pillow), audio + playback."""
import bisect
import collections
import threading
import time

import numpy as np
from PySide6.QtCore import QObject, QSize, Signal
from PySide6.QtGui import QImage

AUDIO_RATE = 48000
PEAK_BLOCK = 256
CACHE_BYTES = 700 * 1024 * 1024
THUMB_H = 72


def _av():
    import av  # imported lazily so plain images open without loading FFmpeg
    av.logging.set_level(av.logging.ERROR)
    return av


def bgra_to_qimage(arr) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, arr.strides[0], QImage.Format_RGB32).copy()


def _ftime(frame, tb, start):
    if frame.pts is not None:
        return frame.pts * tb - start
    return float(frame.time or 0.0) - start


def _rot_k(rotation):
    # PyAV reports the display-matrix angle (counter-clockwise); np.rot90 also turns counter-clockwise.
    return {90: 1, 180: 2, 270: 3}.get(rotation % 360, 0)


def _packet_frames(container, stream):
    """Decode frames, skipping corrupt packets instead of aborting."""
    for packet in container.demux(stream):
        try:
            frames = packet.decode()
        except Exception:
            continue
        for f in frames:
            yield f


def _convert(frame, out_w, out_h, rot_k):
    """Convert to BGRA at a display-oriented size of out_w x out_h."""
    fw, fh = (out_h, out_w) if rot_k in (1, 3) else (out_w, out_h)
    if (fw, fh) == (frame.width, frame.height):
        arr = frame.to_ndarray(format="bgra")
    else:
        arr = frame.reformat(width=fw, height=fh, format="bgra", interpolation="BILINEAR").to_ndarray()
    if rot_k:
        arr = np.rot90(arr, rot_k)
    return bgra_to_qimage(arr)


class VideoSource(QObject):
    """Threaded, frame-accurate video decoder.

    Paused/scrubbing: request(t) -> frameReady(t, img) (latest request wins).
    Playing: play(t) fills a small queue; the UI pulls frames with take_frame(clock).
    """
    frameReady = Signal(float, object)
    thumbReady = Signal()

    kind = "video"

    def __init__(self, path):
        super().__init__()
        av = _av()
        self.path = path
        c = av.open(path)
        self._c = c
        if not c.streams.video:
            c.close()
            raise ValueError("no video stream")
        vs = c.streams.video[0]
        vs.thread_type = "AUTO"
        self._vs = vs
        self.has_audio = bool(c.streams.audio)
        self._tb = float(vs.time_base)
        rate = vs.average_rate or vs.guessed_rate or vs.base_rate
        self.fps = float(rate) if rate else 25.0
        if not 1 <= self.fps <= 1000:
            self.fps = 25.0
        self._start = float(vs.start_time * vs.time_base) if vs.start_time is not None else 0.0
        if vs.duration:
            self.duration = float(vs.duration * vs.time_base)
        elif c.duration:
            self.duration = c.duration / 1_000_000
        else:
            self.duration = 0.0
        self.codec = vs.codec_context.name
        self._iter = _packet_frames(c, vs)
        first = next(self._iter)
        self.rotation = int(getattr(first, "rotation", 0) or 0) % 360
        self._k = _rot_k(self.rotation)
        w, h = first.width, first.height
        if self._k in (1, 3):
            w, h = h, w
        self.size = QSize(w, h)
        if self.duration <= 0:
            self.duration = 1.0 / self.fps
        self.frame_count = max(1, int(round(self.duration * self.fps)))

        self._hint = None
        self._cache = collections.OrderedDict()
        self._cache_bytes = 0
        t0 = _ftime(first, self._tb, self._start)
        self._last_t, self._last_frame = t0, first
        self._last_res = None

        self._cond = threading.Condition()
        self._seek_req = None
        self._playing = False
        self._queue = collections.deque()
        self._gen = 0
        self.eof = False
        self._closed = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

        self.thumb_count = int(min(120, max(10, self.duration * 2)))
        self.thumbs = [None] * self.thumb_count
        self._thumb_thread = threading.Thread(target=self._thumb_run, daemon=True)
        self._thumb_thread.start()

    # ---------------------------------------------------------------- timing helpers
    def index(self, t):
        return max(0, min(self.frame_count - 1, int(round(t * self.fps + 1e-6))))

    def snap(self, t):
        return self.index(t) / self.fps

    def step(self, t, n):
        return max(0, min(self.frame_count - 1, self.index(t) + n)) / self.fps

    # ---------------------------------------------------------------- public API (UI thread)
    def set_hint(self, size: QSize):
        self._hint = size

    def request(self, t):
        with self._cond:
            self._seek_req = t
            self._cond.notify()

    def play(self, t):
        with self._cond:
            self._playing = True
            self._queue.clear()
            self._gen += 1
            self.eof = False
            self._seek_req = t
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._playing = False
            self._queue.clear()
            self._gen += 1
            self._cond.notify()

    def take_frame(self, t):
        with self._cond:
            out = None
            while self._queue and self._queue[0][0] <= t + 0.004:
                out = self._queue.popleft()
            if out is not None:
                self._cond.notify()
            return out

    def queue_empty(self):
        with self._cond:
            return not self._queue

    def full_frame(self, t) -> QImage:
        """Decode the frame at t at full resolution, independently of the playback thread."""
        av = _av()
        c = av.open(self.path)
        try:
            vs = c.streams.video[0]
            vs.thread_type = "AUTO"
            c.seek(max(0, int((t + self._start) / self._tb)), stream=vs, backward=True)
            best = None
            for f in _packet_frames(c, vs):
                ft = _ftime(f, self._tb, self._start)
                if best is None or ft <= t + 0.5 / self.fps:
                    best = f
                if ft >= t - 0.5 / self.fps:
                    break
            return _convert(best, self.size.width(), self.size.height(), self._k)
        finally:
            c.close()

    def close(self):
        with self._cond:
            self._closed = True
            self._cond.notify()
        self._thread.join(timeout=2)
        try:
            self._c.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- decoder thread
    def _out_size(self):
        W, H = self.size.width(), self.size.height()
        hint = self._hint
        if hint is None:
            return W, H
        s = min(1.0, hint.width() / W, hint.height() / H)
        if s > 0.97:
            return W, H
        return max(2, int(W * s) // 2 * 2), max(2, int(H * s) // 2 * 2)

    def _conv(self, frame):
        w, h = self._out_size()
        return _convert(frame, w, h, self._k)

    def _cache_put(self, t, img):
        idx = self.index(t)
        old = self._cache.pop(idx, None)
        if old is not None:
            self._cache_bytes -= old[1].sizeInBytes()
        self._cache[idx] = (t, img)
        self._cache_bytes += img.sizeInBytes()
        while self._cache_bytes > CACHE_BYTES and self._cache:
            _, (_, im) = self._cache.popitem(last=False)
            self._cache_bytes -= im.sizeInBytes()

    def _cached(self, t):
        hit = self._cache.get(self.index(t))
        if hit and hit[1].width() >= self._out_size()[0]:
            self._cache.move_to_end(self.index(t))
            return hit
        return None

    def _seek(self, target):
        ts = int((max(0.0, target) + self._start) / self._tb)
        self._c.seek(max(0, ts), stream=self._vs, backward=True, any_frame=False)
        self._iter = _packet_frames(self._c, self._vs)
        self._last_t = self._last_frame = self._last_res = None

    def _decode_to(self, target, retry=True):
        half = 0.5 / self.fps
        lt = self._last_t
        if self._iter is not None and lt is not None and lt - half <= target <= lt + 2.5:
            if target < lt + half:
                if self._last_res is None:
                    self._last_res = (lt, self._conv(self._last_frame))
                return self._last_res
        else:
            self._seek(target)
        keep_from = target - 1.0
        first = True
        for frame in self._iter:
            t = _ftime(frame, self._tb, self._start)
            if first and retry and t > target + half and target > 0.05 and self._last_t is None:
                # seek landed past the target (imprecise index); go back further
                self._c.seek(max(0, int((max(0.0, target - 3.0) + self._start) / self._tb)),
                             stream=self._vs, backward=True)
                self._iter = _packet_frames(self._c, self._vs)
                self._last_t = self._last_frame = self._last_res = None
                return self._decode_forward(target)
            first = False
            self._last_t, self._last_frame, self._last_res = t, frame, None
            if t >= keep_from:
                self._last_res = (t, self._conv(frame))
                self._cache_put(t, self._last_res[1])
            if t >= target - half:
                return self._last_res
        self._iter = None
        if self._last_frame is not None:
            if self._last_res is None:
                self._last_res = (self._last_t, self._conv(self._last_frame))
            return self._last_res
        return None

    def _decode_forward(self, target):
        half = 0.5 / self.fps
        for frame in self._iter:
            t = _ftime(frame, self._tb, self._start)
            self._last_t, self._last_frame = t, frame
            self._last_res = (t, self._conv(frame)) if t >= target - 1.0 else None
            if self._last_res:
                self._cache_put(t, self._last_res[1])
            if t >= target - half:
                if self._last_res is None:
                    self._last_res = (t, self._conv(frame))
                return self._last_res
        self._iter = None
        if self._last_frame is not None and self._last_res is None:
            self._last_res = (self._last_t, self._conv(self._last_frame))
        return self._last_res

    def _next(self):
        if self._iter is None:
            return None
        try:
            frame = next(self._iter)
        except StopIteration:
            self._iter = None
            return None
        t = _ftime(frame, self._tb, self._start)
        res = (t, self._conv(frame))
        self._last_t, self._last_frame, self._last_res = t, frame, res
        self._cache_put(t, res[1])
        return res

    def _run(self):
        QMAX = 6
        while True:
            with self._cond:
                while not self._closed and self._seek_req is None and not (
                        self._playing and len(self._queue) < QMAX and not self.eof):
                    self._cond.wait()
                if self._closed:
                    return
                target, self._seek_req = self._seek_req, None
                playing, gen = self._playing, self._gen
            try:
                if target is not None:
                    res = None if playing else self._cached(target)
                    if res is None:
                        res = self._decode_to(target)
                    if res is None:
                        continue
                    if playing:
                        with self._cond:
                            if gen == self._gen:
                                self._queue.append(res)
                    else:
                        self.frameReady.emit(res[0], res[1])
                else:
                    res = self._next()
                    with self._cond:
                        if gen != self._gen:
                            continue
                        if res is None:
                            self.eof = True
                        else:
                            self._queue.append(res)
            except Exception as e:  # keep the thread alive on decoder hiccups
                print("decoder error:", e)
                self._iter = None
                self._last_t = None
                with self._cond:
                    if self._playing:
                        self.eof = True

    def _thumb_run(self):
        try:
            av = _av()
            c = av.open(self.path)
            vs = c.streams.video[0]
            vs.thread_type = "AUTO"
            long_video = self.duration > 120
            if long_video:
                vs.codec_context.skip_frame = "NONKEY"
            th = THUMB_H * 2
            tw = max(2, int(th * self.size.width() / self.size.height()) // 2 * 2)
            n = self.thumb_count
            for i in range(n):
                if self._closed:
                    break
                t = (i + 0.5) * self.duration / n
                c.seek(max(0, int((t + self._start) / self._tb)), stream=vs, backward=True)
                img = None
                for f in _packet_frames(c, vs):
                    ft = _ftime(f, self._tb, self._start)
                    if long_video or ft >= t - 0.5 / self.fps:
                        img = _convert(f, tw, th, self._k)
                        break
                if img is not None:
                    self.thumbs[i] = img
                    if i % 4 == 3 or i == n - 1:
                        self.thumbReady.emit()
            c.close()
        except Exception as e:
            print("thumbnail error:", e)
        self.thumbReady.emit()


class AnimSource(QObject):
    """Animated GIF / WebP / APNG fully decoded into memory (Pillow handles frame disposal)."""
    frameReady = Signal(float, object)
    thumbReady = Signal()

    kind = "anim"
    has_audio = False
    rotation = 0
    codec = "animation"

    def __init__(self, path, pil_image):
        super().__init__()
        from PIL import ImageSequence
        self.path = path
        is_gif = (pil_image.format or "").upper() == "GIF"
        self.frames, self.times = [], []
        t = 0.0
        for fr in ImageSequence.Iterator(pil_image):
            d = fr.info.get("duration") or pil_image.info.get("duration") or 100
            if is_gif and d < 20:
                d = 100   # browsers treat tiny GIF delays as 100 ms
            rgba = fr.convert("RGBA")
            img = QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format_RGBA8888)
            self.frames.append(img.convertToFormat(QImage.Format_ARGB32_Premultiplied))
            self.times.append(t)
            t += d / 1000.0
        self.duration = max(t, 0.01)
        self.durations = [b - a for a, b in zip(self.times, self.times[1:] + [self.duration])]
        self.frame_count = len(self.frames)
        self.fps = self.frame_count / self.duration
        self.size = self.frames[0].size()
        self.eof = False
        self._shown = None
        n = min(self.frame_count, 60)
        self.thumb_count = n
        self.thumbs = [self.frames[int((i + 0.5) * self.frame_count / n)].scaledToHeight(THUMB_H * 2)
                       for i in range(n)]

    def index(self, t):
        return max(0, min(self.frame_count - 1, bisect.bisect_right(self.times, t + 1e-6) - 1))

    def snap(self, t):
        return self.times[self.index(t)]

    def step(self, t, n):
        return self.times[max(0, min(self.frame_count - 1, self.index(t) + n))]

    def set_hint(self, size):
        pass

    def request(self, t):
        i = self.index(t)
        self.frameReady.emit(self.times[i], self.frames[i])

    def play(self, t):
        self._shown = None

    def stop(self):
        pass

    def take_frame(self, t):
        i = self.index(t)
        if i == self._shown:
            return None
        self._shown = i
        return self.times[i], self.frames[i]

    def queue_empty(self):
        return True

    def full_frame(self, t):
        return self.frames[self.index(t)]

    def close(self):
        pass


class AudioOnlySource(QObject):
    """Stand-in 'video' for audio files: just provides the time grid."""
    frameReady = Signal(float, object)
    thumbReady = Signal()

    kind = "audio"
    has_audio = True
    rotation = 0
    thumb_count = 0
    thumbs = []

    def __init__(self, path):
        super().__init__()
        av = _av()
        self.path = path
        c = av.open(path)
        if not c.streams.audio:
            c.close()
            raise ValueError("no audio or video stream")
        a = c.streams.audio[0]
        self.codec = a.codec_context.name
        if a.duration:
            self.duration = float(a.duration * a.time_base)
        else:
            self.duration = (c.duration or 0) / 1_000_000
        c.close()
        self.duration = max(self.duration, 0.01)
        self.fps = 100.0
        self.frame_count = max(1, int(round(self.duration * self.fps)))
        self.size = QSize(0, 0)
        self.eof = False

    index = VideoSource.index
    snap = VideoSource.snap
    step = VideoSource.step

    def set_hint(self, size): pass
    def request(self, t): pass
    def play(self, t): pass
    def stop(self): pass
    def take_frame(self, t): return None
    def queue_empty(self): return True
    def full_frame(self, t): return None
    def close(self): pass


class AudioTrack(QObject):
    """Decodes a whole audio track to 48 kHz stereo int16 in the background, plus waveform peaks."""
    loaded = Signal()

    def __init__(self, path, video_start=0.0):
        super().__init__()
        self.path = path
        self.video_start = video_start
        self.data = None
        self.peaks = None
        self.error = None
        self._closed = False
        threading.Thread(target=self._load, daemon=True).start()

    def close(self):
        self._closed = True

    def _load(self):
        try:
            av = _av()
            c = av.open(self.path)
            a = c.streams.audio[0]
            a.thread_type = "AUTO"
            vs_start = 0.0
            if c.streams.video and c.streams.video[0].start_time is not None:
                v = c.streams.video[0]
                vs_start = float(v.start_time * v.time_base)
            rs = av.AudioResampler(format="s16", layout="stereo", rate=AUDIO_RATE)
            chunks, first = [], None
            for frame in _packet_frames(c, a):
                if self._closed:
                    c.close()
                    return
                if first is None and frame.pts is not None:
                    first = float(frame.pts * a.time_base)
                for f in rs.resample(frame):
                    chunks.append(f.to_ndarray().reshape(-1, 2))
            for f in rs.resample(None):
                chunks.append(f.to_ndarray().reshape(-1, 2))
            c.close()
            data = np.concatenate(chunks) if chunks else np.zeros((0, 2), np.int16)
            offset = int(round(((first or 0.0) - vs_start) * AUDIO_RATE))
            if offset > 0:
                data = np.concatenate([np.zeros((offset, 2), np.int16), data])
            elif offset < 0:
                data = data[-offset:]
            self.data = np.ascontiguousarray(data, dtype=np.int16)
            self.peaks = _peaks(self.data)
        except Exception as e:
            self.error = str(e)
            print("audio load error:", e)
        if not self._closed:
            self.loaded.emit()


def _peaks(data):
    n = len(data) // PEAK_BLOCK
    out = np.zeros(n, np.float32)
    step = 4096
    for i in range(0, n, step):
        chunk = data[i * PEAK_BLOCK:(i + step) * PEAK_BLOCK].astype(np.int32)
        m = np.abs(chunk).max(axis=1)
        k = len(m) // PEAK_BLOCK
        out[i:i + k] = m[:k * PEAK_BLOCK].reshape(k, PEAK_BLOCK).max(axis=1) / 32768.0
    return out


class AudioPlayer:
    """Always-open output stream; silent while paused. Provides the master clock while playing."""

    def __init__(self):
        self.data = None
        self.volume = 1.0
        self.muted = False
        self.playing = False
        self.ok = True
        self._pos = 0
        self._lock = threading.Lock()
        self._stream = None
        self._cb_pos = 0
        self._cb_time = None
        self._t0 = 0.0
        self._last_clock = 0.0

    def _ensure(self):
        if self._stream is not None or not self.ok:
            return
        try:
            import sounddevice as sd
            self._stream = sd.OutputStream(samplerate=AUDIO_RATE, channels=2, dtype="int16",
                                           callback=self._cb, latency="low")
            self._stream.start()
        except Exception as e:
            print("audio output unavailable:", e)
            self.ok = False

    def set_data(self, data):
        with self._lock:
            self.playing = False
            self.data = data
        if data is not None:
            self._ensure()

    def _cb(self, out, frames, time_info, status):
        with self._lock:
            if not self.playing or self.data is None:
                out.fill(0)
                return
            p = self._pos
            chunk = self.data[p:p + frames]
            self._pos = p + frames
            self._cb_pos = p
            self._cb_time = time.perf_counter()
        n = len(chunk)
        vol = 0.0 if self.muted else self.volume
        if vol >= 0.999:
            out[:n] = chunk
        elif vol <= 0.001:
            out[:n] = 0
        else:
            out[:n] = (chunk * vol).astype(np.int16)
        out[n:] = 0

    @property
    def active(self):
        return self.ok and self._stream is not None and self.data is not None

    def start(self, t):
        with self._lock:
            self._pos = max(0, int(t * AUDIO_RATE))
            self._t0 = t
            self._last_clock = t
            self._cb_time = None
            self.playing = True

    def stop(self):
        with self._lock:
            self.playing = False

    def clock(self):
        with self._lock:
            if self._cb_time is None:
                return self._t0
            lat = self._stream.latency if self._stream is not None else 0.0
            c = self._cb_pos / AUDIO_RATE + (time.perf_counter() - self._cb_time) - lat
            c = max(c, self._last_clock)
            self._last_clock = c
            return c

    def close(self):
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
