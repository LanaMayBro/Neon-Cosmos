"""
NeonCosmos - GPU-скринсейвер для Windows (OpenGL): "Рождение вселенной".
Из чёрной пустоты вспыхивает точка, разлетаются частицы, разрастается газовая туманность
(малиново-розовая, розово-белая, красная с пузырём, сиреневая, огненная), зажигаются звёзды -
потом она растворяется, и рождается следующая - на соседнем мониторе, не на стыке. На басах музыки - мягкие пульсации.
Изредка пролетает комета с двойным хвостом (газовым и пылевым).
Редко, раз в несколько минут, медленно проплывает чёрная дыра: она искривляет свет звёзд и туманности (гравитационное линзирование).

Зависимости:
    pip install pygame-ce numpy moderngl pyaudiowpatch

Запуск:
    python neon_cosmos.py              - показать заставку на всех мониторах (для отладки)
    python neon_cosmos.py /s           - то же самое
    python neon_cosmos.py window       - ТЕСТ В ОКНЕ: S = скриншот, N = сразу к следующей туманности, H = чёрная дыра сейчас, C = комета сейчас, Esc = выход
    python neon_cosmos.py /s shots     - заставка + автоматические скриншоты на Рабочий стол (папка neon_shots)
    python neon_cosmos.py /c           - окно настроек

Логи: C:\\Users\\<имя>\\.neon_cosmos.log  (там же название видеокарты и FPS)

Сборка в .scr:
    python -m PyInstaller --onefile --noconsole --hidden-import pyaudiowpatch --hidden-import moderngl --collect-all moderngl --name NeonCosmos neon_cosmos.py
    затем dist\\NeonCosmos.exe переименовать в NeonCosmos.scr и скопировать в C:\\Windows\\System32
"""
import sys
import os
import json
import math
import time
import ctypes
import random
import logging
import threading
from ctypes import wintypes

import numpy as np
import pygame
import moderngl
import tkinter as tk
from tkinter import ttk

try:  # импорт на верхнем уровне, чтобы PyInstaller точно включил модуль в .exe
    import pyaudiowpatch as pyaudio
except ImportError:
    pyaudio = None

# ---------------------------------------------------------------------------
# Логи
# ---------------------------------------------------------------------------
HOME = os.path.expanduser("~")
logging.basicConfig(
    filename=os.path.join(HOME, ".neon_cosmos.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

# ---------------------------------------------------------------------------
# DPI и виртуальный экран (все мониторы сразу)
# ---------------------------------------------------------------------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
VIR_X = user32.GetSystemMetrics(76)
VIR_Y = user32.GetSystemMetrics(77)
VIR_W = user32.GetSystemMetrics(78)
VIR_H = user32.GetSystemMetrics(79)

CONFIG_FILE = os.path.join(HOME, ".neon_cosmos_config.json")
DEFAULT_CONFIG = {
    "num_particles": 60000,
    "speed": 1.0,
    "cycle": 45.0,      # секунд на одну туманность
    "glow": 1.0,
    "lens": 1.0,         # сила чёрной дыры (0 = выключена)
    "lens_every": 150.0, # раз в сколько секунд проплывает чёрная дыра
    "comets": 1.0,       # яркость комет (0 = выключены)
    "comet_every": 80.0, # раз в сколько секунд пролетает комета
}

# ---------------------------------------------------------------------------
# Настройки внешнего вида (можно крутить)
# ---------------------------------------------------------------------------
AUDIO_DELAY = 5.0      # аудио подключается через столько секунд после старта
INTENSITY = 0.60       # яркость частиц взрыва
EXPOSURE = 1.20        # общая экспозиция
NEB_GAIN = 0.85        # яркость газа туманностей
NEB_DIV = 3            # газ считается в разрешении экрана / NEB_DIV (больше = легче для видеокарты)
SCENE_SCALE = 1.0      # масштаб рендера (0.75 = легче для слабой видеокарты)
BEAT_STRENGTH = 0.7    # сила реакции на басы: 0 = выкл, 1 = как задумано
LENS_SIZE = 0.11       # радиус кольца Эйнштейна (в высотах экрана)
LENS_FIRST = 25.0      # когда впервые проплывёт чёрная дыра (секунды после старта)
LENS_LIFE = 70.0       # сколько секунд она пересекает монитор
COMET_FIRST = 40.0     # когда впервые пролетит комета
COMET_LIFE = 36.0      # сколько секунд комета пересекает монитор


def load_config():
    cfg = DEFAULT_CONFIG.copy()
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
    except Exception:
        logging.exception("Не удалось прочитать конфиг")
    cfg["num_particles"] = int(min(300000, max(5000, cfg["num_particles"])))
    cfg["speed"] = float(min(5.0, max(0.1, cfg["speed"])))
    cfg["cycle"] = float(min(300.0, max(15.0, cfg["cycle"])))
    cfg["glow"] = float(min(3.0, max(0.1, cfg["glow"])))
    cfg["lens"] = float(min(2.0, max(0.0, cfg["lens"])))
    cfg["lens_every"] = float(min(900.0, max(60.0, cfg["lens_every"])))
    cfg["comets"] = float(min(2.0, max(0.0, cfg["comets"])))
    cfg["comet_every"] = float(min(600.0, max(40.0, cfg["comet_every"])))
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        logging.exception("Не удалось сохранить конфиг")


def already_running():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW(None, False, "NeonCosmosScreensaverMutex")
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


# ---------------------------------------------------------------------------
# Аудио: loopback (звук из колонок) + детектор ударов
# ---------------------------------------------------------------------------
class AudioAnalyzer:
    def __init__(self):
        self.bass = 0.0
        self.volume = 0.0
        self.beat = 0.0
        self.last_update = 0.0
        self._avg_bass = 0.0
        self._peak_bass = 0.0005
        self._peak_vol = 0.01
        self._channels = 2
        self._rate = 44100
        self._pa = None
        self._stream = None

    def start(self):
        threading.Thread(target=self._open, daemon=True).start()

    def _open(self):
        try:
            if pyaudio is None:
                raise RuntimeError("модуль pyaudiowpatch не найден: pip install pyaudiowpatch")
            pa = pyaudio.PyAudio()
            wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)
            speakers = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
            if not speakers.get("isLoopbackDevice"):
                found = None
                for lb in pa.get_loopback_device_info_generator():
                    if speakers["name"] in lb["name"]:
                        found = lb
                        break
                if found is None:
                    raise RuntimeError("Loopback-устройство для колонок не найдено")
                speakers = found
            self._channels = int(speakers["maxInputChannels"])
            self._rate = int(speakers["defaultSampleRate"])
            self._stream = pa.open(
                format=pyaudio.paInt16,
                channels=self._channels,
                rate=self._rate,
                input=True,
                input_device_index=speakers["index"],
                frames_per_buffer=1024,
                stream_callback=self._callback,
            )
            self._pa = pa
            logging.info("Аудио loopback запущен: %s", speakers["name"])
        except Exception:
            logging.exception("Не удалось запустить аудио (заставка будет без реакции на звук)")

    def _callback(self, in_data, frame_count, time_info, status):
        try:
            s = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
            if self._channels > 1:
                s = s[: len(s) // self._channels * self._channels]
                s = s.reshape(-1, self._channels).mean(axis=1)
            n = len(s)
            if n < 64:
                return (None, 0)
            vol = float(np.sqrt(np.mean(s * s)))
            spec = np.abs(np.fft.rfft(s * np.hanning(n))) / n
            hz_per_bin = self._rate / n
            hi = max(3, int(150.0 / hz_per_bin))  # басы: до ~150 Гц
            bass = float(np.mean(spec[1:hi]))

            self._peak_vol = max(vol, self._peak_vol * 0.995, 0.01)
            self._peak_bass = max(bass, self._peak_bass * 0.995, 0.0005)
            self.volume = min(1.5, vol / self._peak_vol)
            self.bass = min(1.5, bass / self._peak_bass)
            # детектор удара: энергия баса резко выше своего среднего за ~1 с
            if self._avg_bass == 0.0:
                self._avg_bass = bass
            elif bass > 0.12 * self._peak_bass:
                hit = (bass / self._avg_bass - 1.12) * 2.5
                self.beat = max(self.beat, min(1.5, max(0.0, hit)))
            self._avg_bass += (bass - self._avg_bass) * 0.03
            self.last_update = time.monotonic()
        except Exception:
            logging.exception("Ошибка в audio callback")
        return (None, 0)  # 0 = paContinue

    def stop(self):
        try:
            if self._stream is not None:
                self._stream.stop_stream()
                self._stream.close()
            if self._pa is not None:
                self._pa.terminate()
        except Exception:
            pass


def smooth(cur, new, attack=0.6, release=0.08):
    """Быстро растём на ударе, медленно затухаем."""
    return cur + (new - cur) * (attack if new > cur else release)


# ---------------------------------------------------------------------------
# Шейдеры
# ---------------------------------------------------------------------------
COMMON_GLSL = """
const float TAU = 6.2831853;

uint pcg(uint v) {
    uint state = v * 747796405u + 2891336453u;
    uint word = ((state >> ((state >> 28u) + 4u)) ^ state) * 277803737u;
    return (word >> 22u) ^ word;
}
float rnd(uint s) { return float(pcg(s)) * (1.0 / 4294967296.0); }

// Палитры пяти туманностей: cA - тёмная пыль, cB - основной газ, cC - горячие светлые места, cD - акцентный цвет
void palette(int i, out vec3 cA, out vec3 cB, out vec3 cC, out vec3 cD) {
    if (i == 0)      { cA = vec3(0.26, 0.04, 0.16); cB = vec3(0.90, 0.22, 0.55); cC = vec3(1.00, 0.70, 0.52); cD = vec3(0.25, 0.35, 1.00); }  // малиново-розовая + синий
    else if (i == 1) { cA = vec3(0.18, 0.04, 0.12); cB = vec3(0.74, 0.22, 0.46); cC = vec3(1.00, 0.88, 0.94); cD = vec3(0.35, 0.40, 1.00); }  // розово-белое ядро + сине-фиолетовый
    else if (i == 2) { cA = vec3(0.20, 0.05, 0.09); cB = vec3(0.88, 0.22, 0.20); cC = vec3(0.80, 0.78, 0.92); cD = vec3(0.55, 0.60, 0.80); }  // красные нити + серо-голубой, пузырь
    else if (i == 3) { cA = vec3(0.05, 0.04, 0.24); cB = vec3(0.36, 0.30, 0.96); cC = vec3(0.78, 0.74, 1.00); cD = vec3(0.75, 0.30, 0.90); }  // сиренево-голубая
    else             { cA = vec3(0.26, 0.05, 0.03); cB = vec3(0.92, 0.32, 0.18); cC = vec3(0.88, 1.00, 0.80); cD = vec3(0.45, 0.90, 0.65); }  // огненная + зеленоватое свечение
}
"""

# Взрыв при рождении туманности: частицы вылетают из центра, тормозят и закручиваются в нити.
PARTICLE_VS = """
#version 330
uniform vec2  u_res;
uniform float u_aspect;
uniform vec4  u_ev0;       // (x, y, возраст, зерно) для чётных туманностей
uniform vec4  u_ev1;       // то же для нечётных
uniform float u_pal0;
uniform float u_pal1;
uniform float u_beat;
uniform float u_intensity;

out vec2  v_uv;
out vec3  v_col;
out float v_alpha;

const int IDX[6] = int[6](0, 1, 2, 2, 1, 3);
__COMMON__

vec2 burstPos(vec2 c, float t, float ang0, float rInf, float T0, float sw) {
    float tt = max(t, 0.0);
    float r = rInf * (1.0 - exp(-tt / T0)) + 0.004 * tt;
    float a = ang0 + sw * r;
    return c + r * vec2(cos(a), sin(a));
}

void main() {
    int vid = gl_VertexID;
    uint pid = uint(vid / 6);
    int cc = IDX[vid % 6];
    float along = float(cc >> 1);
    float side = ((cc & 1) == 0) ? -1.0 : 1.0;

    bool odd = ((pid & 1u) == 1u);
    vec4 ev = odd ? u_ev1 : u_ev0;
    float pal = odd ? u_pal1 : u_pal0;

    uint b = pid * 8u;
    float r0 = rnd(b),      r1 = rnd(b + 1u), r2 = rnd(b + 2u), r3 = rnd(b + 3u);
    float r4 = rnd(b + 4u), r5 = rnd(b + 5u), r6 = rnd(b + 6u), r7 = rnd(b + 7u);

    float tn = ev.z;
    float r04 = r0 * r0 * r0 * r0;
    float tp = tn - (2.5 * r0 + 1.1 * r04);         // старт взрыва растянут по времени: часть частиц вылетает позже, центр не пустеет
    float ang0 = r1 * TAU;
    float rInf = 0.12 + 1.30 * r2 * r2;             // докуда долетит (в высотах экрана)
    float T0 = 2.4 + 3.2 * r3;                      // как быстро тормозит
    float sw = (r4 - 0.25) * 2.6;                   // закрутка в спирали

    float dts = 0.22 * (1.0 + 0.8 * u_beat);
    vec2 hw = burstPos(ev.xy, tp, ang0, rInf, T0, sw);
    vec2 tw = burstPos(ev.xy, tp - dts, ang0, rInf, T0, sw);
    vec2 hp = vec2(hw.x * 2.0 / u_aspect, hw.y * 2.0) * 0.5 * u_res;
    vec2 tpx = vec2(tw.x * 2.0 / u_aspect, tw.y * 2.0) * 0.5 * u_res;
    vec2 dir = hp - tpx;
    float L = length(dir);
    // у только что вылетевших частиц голова и хвост совпадают: направляем штрих от центра взрыва
    vec2 cpx = vec2(ev.x * 2.0 / u_aspect, ev.y * 2.0) * 0.5 * u_res;
    vec2 rad = hp - cpx;
    float rl = length(rad);
    vec2 rdir = (rl > 1e-4) ? rad / rl : vec2(cos(ang0), sin(ang0));
    dir = (L > 1e-3) ? dir / L : rdir;
    float L2 = clamp(L, 3.0, 300.0);
    vec2 tail = hp - dir * L2;

    float spark = step(0.97, r5);
    float wpx = (1.2 + 2.2 * spark) * (u_res.y / 1080.0);
    vec2 nrm = vec2(-dir.y, dir.x);
    vec2 center = mix(tail, hp, along);
    float wc = mix(0.35, 1.0, along) * wpx;
    vec2 pix = center + nrm * side * wc * 0.5 + dir * along * wpx * 0.5;
    gl_Position = vec4(pix / (0.5 * u_res), 0.0, 1.0);

    float life = smoothstep(0.0, 0.30, tp) * (1.0 - smoothstep(34.0, 54.0, tp));
    float vis = (tn >= 0.0 && tp > 0.0) ? 1.0 : 0.0;
    v_alpha = life * vis;
    v_uv = vec2(side, along);

    vec3 cA, cB, cC, cD;
    palette(int(pal + 0.5), cA, cB, cC, cD);
    float hot = exp(-max(tp, 0.0) / 2.2);
    vec3 col = mix(cB, cC, r6);
    col = mix(col, vec3(1.0, 0.93, 0.80), hot * 0.85);
    float bright = (0.25 + 0.75 * r7 * r7) * (1.0 + 1.3 * hot + 1.6 * spark) * (1.0 + 0.35 * u_beat);
    v_col = col * bright * u_intensity;
}
"""

PARTICLE_FS = """
#version 330
in vec2  v_uv;
in vec3  v_col;
in float v_alpha;
out vec4 f_color;
void main() {
    float across = max(0.0, 1.0 - v_uv.x * v_uv.x);
    float tailFade = smoothstep(0.0, 1.0, v_uv.y);
    float y2 = v_uv.y * v_uv.y;
    float head = y2 * y2 * y2;
    float a = across * (0.25 * tailFade + 0.9 * head) * v_alpha;
    vec3 c = v_col * a + vec3(1.0) * head * across * across * 0.18 * v_alpha * dot(v_col, vec3(0.33));
    f_color = vec4(c, 1.0);
}
"""

# Газ туманностей. У каждой из пяти туманностей своя композиция (как на настоящих снимках):
#   0 - большое облако сбоку + маленькие облачка отдельно + яркая синяя звезда с голубым ореолом
#   1 - компактное белое ядро с тёмной пылевой полосой + маленькая голубая туманность над ним
#   2 - большое рассеянное облако с тонким "пузырём"
#   3 - изогнутая сиренево-голубая лента (как "Ведьмина голова")
#   4 - плотные красные нити с кремово-зелёной серединой
NEBULA_FS = """
#version 330
uniform float u_aspect;
uniform float u_nsc;       // масштаб композиции под размер монитора
uniform vec4  u_lens;      // (x, y, радиус кольца Эйнштейна, видимость 0..1) в координатах сцены
// гравитационная линза: откуда на самом деле пришёл свет, видимый в точке p
vec2 lensP(vec2 p) {
    float te = u_lens.z;
    if (te <= 0.0) return p;
    vec2 d = p - u_lens.xy;
    float r2 = max(dot(d, d), 0.09 * te * te);
    return p - d * (te * te / r2);
}
uniform float u_tg;        // время течения газа
uniform vec4  u_ev0;
uniform vec4  u_ev1;
uniform float u_pal0;
uniform float u_pal1;
in vec2 v_uv;
out vec4 f_color;
__COMMON__

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}
float vnoise(vec2 p) {
    vec2 i = floor(p), f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hash21(i), b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0)), d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
}
float fbm(vec2 p) {
    float s = 0.0, a = 0.5;
    mat2 m = mat2(1.6, 1.2, -1.2, 1.6);
    for (int i = 0; i < 5; i++) { s += a * vnoise(p); p = m * p; a *= 0.5; }
    return s;
}
mat2 rot2(float a) { float c = cos(a), s = sin(a); return mat2(c, s, -s, c); }

// как быстро "вырос" элемент туманности: delay - когда он начал расти, sp - за сколько
float gr(float tn, float delay, float sp) { return 1.0 - exp(-max(tn - delay, 0.0) / sp); }

// текстура газа: f - плотность, ridge - тонкие светящиеся нити, lane - тёмные пылевые прожилки
void gasNoise(vec2 q, float sd, float tg, out float f, out float ridge, out float lane) {
    vec2 qq = q + vec2(sd * 3.1, sd * 1.7);
    vec2 w1 = vec2(fbm(qq + vec2(0.0, tg * 0.06)), fbm(qq + vec2(5.2, 1.3) - vec2(tg * 0.05, 0.0)));
    vec2 w2 = vec2(fbm(qq + 3.0 * w1 + vec2(1.7, 9.2) + tg * 0.04), fbm(qq + 3.0 * w1 + vec2(8.3, 2.8) - tg * 0.03));
    f = fbm(qq + 3.2 * w2);
    ridge = 1.0 - abs(2.0 * fbm(qq * 2.4 + w2 * 2.8 + 4.0) - 1.0);
    lane = fbm(qq * 1.5 + w1 * 2.2 + 11.0);
}

// клуб газа: рождается в центре и вырастает на своё место c; size - полуоси; возвращает маску
float gasLobe(vec2 l, vec2 c, vec2 size, float g, float sd, float tg, out float f, out float ridge, out float lane) {
    f = 0.0; ridge = 0.0; lane = 0.0;
    if (g <= 0.002) return 0.0;
    vec2 q = (l - c * g) / (size * g);
    float r2 = dot(q, q);
    if (r2 > 2.4) return 0.0;
    gasNoise(q * 1.9, sd, tg, f, ridge, lane);
    float edge = 1.0 + 0.55 * (f - 0.5);
    float mask = 1.0 - smoothstep(0.12, edge, sqrt(r2));
    return mask * smoothstep(0.10, 0.45, f + 0.45 * mask);
}

// окраска клуба: тёмная пыль -> основной газ -> акцентный цвет -> горячие места, нити и пылевые прожилки
vec3 shade(float f, float ridge, float lane, float m, vec3 cA, vec3 cB, vec3 cC, vec3 cD,
           float blueAmt, float hotAmt, float gain) {
    float dens = m * smoothstep(0.28, 1.0, f * 1.35 + 0.15 * m);
    vec3 col = mix(cA, cB, smoothstep(0.22, 0.62, f));
    col = mix(col, cD, blueAmt * smoothstep(0.45, 0.75, f));
    col = mix(col, cC, hotAmt * smoothstep(0.55, 0.95, f));
    col *= dens * gain;
    float rg = max(ridge, 0.0);
    rg = rg * rg * rg * rg * rg;
    col += cC * rg * m * 0.40;
    col *= 1.0 - 0.85 * smoothstep(0.50, 0.70, lane) * m;
    return col;
}

// 0: большое розово-оранжевое облако сбоку, маленькие облачка отдельно, голубое облако вокруг яркой звезды
vec3 kindLagoon(vec2 l, float tn, float sd, float tg) {
    vec3 cA, cB, cC, cD;
    palette(0, cA, cB, cC, cD);
    vec3 col = vec3(0.0);
    float f, rg, ln, m;
    m = gasLobe(l, vec2(-0.34, -0.06), vec2(0.52, 0.44), gr(tn, 0.0, 6.0), sd, tg, f, rg, ln);
    col += shade(f, rg, ln, m, cA, cB, cC, cD, 0.30, 0.70, 1.6);
    m = gasLobe(l, vec2(0.52, 0.22), vec2(0.20, 0.17), gr(tn, 3.0, 5.0), sd + 3.0, tg, f, rg, ln);
    col += shade(f, rg, ln, m, cA, cB * 0.9, cC, cD, 0.50, 0.55, 1.5);
    m = gasLobe(l, vec2(0.42, -0.20), vec2(0.15, 0.12), gr(tn, 5.0, 5.0), sd + 6.0, tg, f, rg, ln);
    col += shade(f, rg, ln, m, cA, cB, cC, cD, 0.40, 0.55, 1.4);
    m = gasLobe(l, vec2(0.10, -0.34), vec2(0.13, 0.10), gr(tn, 7.0, 5.0), sd + 9.0, tg, f, rg, ln);
    col += shade(f, rg, ln, m, cA, cB, cC, cD, 0.40, 0.55, 1.3);
    // голубое отражательное облако вокруг яркой звезды
    float gg = max(gr(tn, 2.0, 5.0), 0.02);
    vec2 dh = l - vec2(0.03, 0.14);
    float rr = length(dh);
    float haloN = 0.55 + 0.9 * fbm(dh * 7.0 + sd);
    col += cD * (1.1 * exp(-rr / (0.07 * gg)) + 0.30 * exp(-rr / (0.20 * gg))) * haloN;
    return col;
}

// 1: яркое компактное ядро, тёмная пылевая полоса и маленькая голубая туманность над ним
vec3 kindOrion(vec2 l, float tn, float sd, float tg) {
    vec3 cA, cB, cC, cD;
    palette(1, cA, cB, cC, cD);
    vec3 col = vec3(0.0);
    float f, rg, ln, m;
    m = gasLobe(l, vec2(0.0, -0.05), vec2(0.66, 0.52), gr(tn, 0.0, 8.0), sd + 2.0, tg, f, rg, ln);
    col += shade(f, rg, ln, m, cA * 0.6, cB * 0.55, cB, cD, 0.25, 0.10, 0.80);
    float g0 = max(gr(tn, 0.0, 6.0), 0.02);
    m = gasLobe(l, vec2(0.0, -0.10), vec2(0.30, 0.27), g0, sd, tg, f, rg, ln);
    col += shade(f, rg, ln, m, cA, cB, cC, cD, 0.15, 0.95, 2.0);
    vec2 dc = l - vec2(-0.03, -0.07) * g0;
    col += cC * 1.5 * exp(-dot(dc, dc) / (0.010 * g0 * g0));           // белое ядро
    vec3 bA = vec3(0.10, 0.07, 0.28), bB = vec3(0.38, 0.42, 1.00), bC = vec3(0.80, 0.85, 1.00), bD = vec3(0.75, 0.18, 0.20);
    m = gasLobe(l, vec2(0.03, 0.32), vec2(0.16, 0.12), gr(tn, 4.0, 5.0), sd + 5.0, tg, f, rg, ln);
    col += shade(f, rg, ln, m, bA, bB, bC, bD, 0.35, 0.60, 1.7);
    float yy = (l.y - 0.19 - 0.04 * sin(l.x * 9.0 + sd)) / 0.045;
    float dust = exp(-yy * yy) * smoothstep(0.0, 0.4, gr(tn, 2.0, 4.0));
    col *= 1.0 - 0.75 * dust;                                            // тёмная полоса между ядром и голубой туманностью
    return col;
}

// 2: большое рассеянное облако с тонким "пузырём"
vec3 kindBubble(vec2 l, float tn, float sd, float tg) {
    vec3 cA, cB, cC, cD;
    palette(2, cA, cB, cC, cD);
    float grow = 1.0 - exp(-tn / 7.0);
    vec2 d = l / vec2(1.25, 1.0);
    float rr = length(d);
    float f, rg, ln;
    gasNoise(d * (2.0 / (0.30 + grow)), sd, tg, f, rg, ln);
    float rad = 0.56 * grow * (0.70 + 0.60 * f);
    float mask = 1.0 - smoothstep(0.10 * rad, rad, rr);
    mask *= smoothstep(0.10, 0.45, f + 0.45 * mask);
    float lit = 0.45 + 1.0 * fbm(d * 1.1 + sd + 30.0);
    vec3 col = shade(f, rg, ln, mask, cA, cB, cC, cD, 0.45, 0.45, 1.35) * lit;
    float Rb = 0.30 * grow;
    float e = (rr - Rb) / (0.016 + 0.020 * f);
    col += vec3(0.78, 0.82, 0.95) * exp(-e * e) * 0.40 * smoothstep(0.2, 0.7, grow) * smoothstep(0.30, 0.60, f + 0.25 * ln);
    return col;
}

// 3: изогнутая сиренево-голубая лента с более толстой "головой", рваная и волокнистая
vec3 kindWitch(vec2 l, float tn, float sd, float tg) {
    vec3 cA, cB, cC, cD;
    palette(3, cA, cB, cC, cD);
    float g = gr(tn, 0.0, 7.0);
    vec2 u = rot2(0.5) * l;
    float f, rg, ln;
    gasNoise(l * 2.3, sd, tg, f, rg, ln);
    float bend = 0.17 * sin(u.x * 2.6 + 0.8) + 0.10 * u.x * u.x + 0.05 * (fbm(vec2(u.x * 3.0, sd)) - 0.5);
    float v = u.y - bend;
    float body = 0.05 + 0.17 * smoothstep(-0.65, 0.10, u.x);
    float wid = body * (1.0 - smoothstep(0.30, 0.80, u.x)) * (0.55 + 0.95 * f) * smoothstep(0.0, 0.5, g) + 0.003;
    float vv = abs(v) / wid;
    float reach = -0.62 + 1.50 * g;
    float m = (1.0 - smoothstep(0.0, 1.15 + 0.8 * (f - 0.5), vv)) * (1.0 - smoothstep(reach - 0.14, reach, u.x));
    m *= smoothstep(0.08, 0.50, f + 0.35 * m);
    m *= smoothstep(-0.64, -0.46, u.x);                       // левый конец ленты не уходит за пределы монитора
    float lit = 0.30 + 1.25 * fbm(l * 2.6 + vec2(sd * 1.3, sd * 0.7) + 40.0);
    vec3 col = shade(f, rg, ln, m, cA, cB, cC, cD, 0.35, 0.55, 1.7) * lit;
    float haze = exp(-v * v / 0.10) * smoothstep(0.0, 0.6, g) * (1.0 - smoothstep(reach - 0.2, reach, u.x)) * smoothstep(-0.64, -0.46, u.x);
    col += cB * haze * (0.05 + 0.12 * f);
    return col;
}

// 4: плотные красные нити с кремово-зелёной серединой и яркой россыпью звёзд внизу
vec3 kindFlame(vec2 l, float tn, float sd, float tg) {
    vec3 cA, cB, cC, cD;
    palette(4, cA, cB, cC, cD);
    float f, rg, ln, m;
    m = gasLobe(l, vec2(0.0, 0.02), vec2(0.66, 0.46), gr(tn, 0.0, 7.0), sd, tg, f, rg, ln);
    vec3 col = shade(f, rg, ln, m, cA, cB, cC, cD, 0.18, 0.55, 1.25);
    float r3 = max(rg, 0.0);
    r3 = r3 * r3 * r3;
    col += mix(cB, cC, f) * r3 * m * 0.55;
    float g = max(gr(tn, 3.0, 5.0), 0.02);
    vec2 dc = l - vec2(0.0, -0.27) * g;
    col += mix(cB, cC, 0.5) * 0.75 * exp(-dot(dc, dc) / (0.008 * g * g));
    return col;
}

vec3 nebula(vec2 p, vec4 ev, float pal) {
    float tn = ev.z;
    if (tn < 0.0) return vec3(0.0);
    float amp = smoothstep(0.0, 4.0, tn) * (1.0 - smoothstep(38.0, 56.0, tn));
    if (amp <= 0.001) return vec3(0.0);

    vec2 l = (p - ev.xy) / u_nsc;
    int ip = int(pal + 0.5);
    vec3 col;
    if (ip == 0)      col = kindLagoon(l, tn, ev.w, u_tg);
    else if (ip == 1) col = kindOrion(l, tn, ev.w, u_tg);
    else if (ip == 2) col = kindBubble(l, tn, ev.w, u_tg);
    else if (ip == 3) col = kindWitch(l, tn, ev.w, u_tg);
    else              col = kindFlame(l, tn, ev.w, u_tg);

    // молодое горячее ядро: вспыхивает при рождении и затухает
    float grow = 1.0 - exp(-tn / 7.0);
    float rr = length(l);
    float core = exp(-rr * rr / (0.010 + 0.035 * grow));
    float flash = smoothstep(0.0, 0.7, tn) * exp(-tn / 3.8) * 1.15;
    col += vec3(1.0, 0.92, 0.80) * core * flash;
    return col * amp;
}

void main() {
    vec2 p0 = (v_uv - 0.5) * vec2(u_aspect, 1.0);
    vec2 p = lensP(p0);
    vec3 c = nebula(p, u_ev0, u_pal0) + nebula(p, u_ev1, u_pal1);
    if (u_lens.z > 0.0) {
        // линза усиливает яркие места - возле дыры мягко приглушаем, чтобы не было белых пятен
        vec2 dd = (p0 - u_lens.xy) / (2.4 * u_lens.z);
        float w = exp(-dot(dd, dd));
        c = mix(c, c / (1.0 + 1.1 * dot(c, vec3(0.333))), w);
    }
    f_color = vec4(c, 1.0);
}
"""

# Звёздное поле: слои мелких звёзд (где-то гуще, где-то реже - зависит от туманности) и
# яркие "именные" звёзды с лучами, которые зажигаются на своих местах в каждой туманности.
STARS_FS = """
#version 330
uniform vec2  u_res;
uniform float u_aspect;
uniform float u_nsc;
uniform float u_time;
uniform vec4  u_cA;        // комета: (x, y, видимость, масштаб)
uniform vec4  u_cB;        // направление хвоста T (xy) и скорости V (zw), единичные
uniform float u_cbass;
uniform vec4  u_lens;      // (x, y, радиус кольца Эйнштейна, видимость 0..1) в координатах сцены
// гравитационная линза: откуда на самом деле пришёл свет, видимый в точке p
vec2 lensP(vec2 p) {
    float te = u_lens.z;
    if (te <= 0.0) return p;
    vec2 d = p - u_lens.xy;
    float r2 = max(dot(d, d), 0.09 * te * te);
    return p - d * (te * te / r2);
}
uniform float u_starfade;
uniform vec4  u_ev0;
uniform vec4  u_ev1;
uniform float u_pal0;
uniform float u_pal1;
uniform sampler2D u_neb;
in vec2 v_uv;
out vec4 f_color;
__COMMON__

vec3 starLayer(vec2 px, float cell, float prob, float size, float spikes, float seedv, float nebL) {
    vec2 g = floor(px / cell);
    vec3 acc = vec3(0.0);
    for (int j = -1; j <= 1; j++) {
        for (int i = -1; i <= 1; i++) {
            vec2 gc = g + vec2(float(i), float(j));
            uint h = pcg(uint(int(gc.x) + 16384) * 1973u + uint(int(gc.y) + 16384) * 9277u + uint(seedv));
            float r0 = float(h) * (1.0 / 4294967296.0);
            if (r0 > prob) continue;
            uint h2 = pcg(h ^ 0x68E31DA4u);
            float r1 = float(h2) * (1.0 / 4294967296.0);
            uint h3 = pcg(h2);
            float r2 = float(h3) * (1.0 / 4294967296.0);
            uint h4 = pcg(h3);
            float r3 = float(h4) * (1.0 / 4294967296.0);
            uint h5 = pcg(h4);
            float r4 = float(h5) * (1.0 / 4294967296.0);
            vec2 sp = (gc + vec2(r1, r2)) * cell;
            vec2 d = px - sp;
            float bb = 0.20 + 0.80 * r3 * r3 * r3;
            float tw = 0.88 + 0.12 * sin(u_time * (0.8 + 3.0 * r4) + r1 * TAU);
            float s2 = size * size * (0.6 + r4);
            float core = exp(-dot(d, d) / s2);
            float rays = 0.0;
            if (spikes > 0.0 && r3 > 0.80) {
                float L = size * 14.0 * r3;
                rays = 0.5 * spikes * (exp(-abs(d.x) / L) * exp(-abs(d.y) / (size * 0.5))
                                     + exp(-abs(d.y) / L) * exp(-abs(d.x) / (size * 0.5)));
            }
            vec3 tint = (r4 < 0.45) ? vec3(0.72, 0.82, 1.0)
                      : (r4 < 0.75) ? vec3(1.0, 0.97, 0.92)
                      : (r4 < 0.92) ? vec3(1.0, 0.80, 0.55) : vec3(1.0, 0.62, 0.52);
            acc += tint * bb * tw * (core + rays);
        }
    }
    return acc * (1.0 + 0.9 * clamp(nebL * 3.0, 0.0, 1.0));
}


float hash2(vec2 i) {
    uint h = pcg(uint(int(i.x) + 4096) * 1973u + uint(int(i.y) + 4096) * 9277u);
    return float(h) * (1.0 / 4294967296.0);
}
float vnoise(vec2 x) {
    vec2 i = floor(x), f = fract(x);
    f = f * f * (3.0 - 2.0 * f);
    return mix(mix(hash2(i), hash2(i + vec2(1.0, 0.0)), f.x),
               mix(hash2(i + vec2(0.0, 1.0)), hash2(i + vec2(1.0, 1.0)), f.x), f.y);
}

float fbm2(vec2 x) {
    return 0.55 * vnoise(x) + 0.30 * vnoise(x * 2.1 + 5.3) + 0.15 * vnoise(x * 4.3 + 11.7);
}

// комета: туманная пульсирующая голова с комой, рваный голубой газовый хвост и широкий изогнутый жёлтый пылевой
vec3 comet(vec2 p) {
    float env = u_cA.z;
    if (env <= 0.001) return vec3(0.0);
    float S = u_cA.w;
    vec2 d = p - u_cA.xy;
    vec2 T = u_cB.xy, V = u_cB.zw;
    vec2 P = vec2(-T.y, T.x);
    float a = dot(d, T) / S;
    float b = dot(d, P) / S;
    float r2 = dot(d, d) / (S * S);
    float r = sqrt(r2);

    // голова: мягкое ядро и неровная кома, которая дышит на басах
    float pulse = 1.0 + 0.30 * u_cbass;
    float ang = atan(d.y, d.x);
    float cn = 0.75 + 0.5 * vnoise(vec2(ang * 1.7 + u_time * 0.35, r * 18.0 - u_time * 0.5));
    vec3 col = vec3(1.0, 0.96, 0.88) * (1.15 * exp(-r2 / (0.00009 * pulse))
                                     + 0.50 * cn * exp(-r2 / (0.0011 * pulse))
                                     + 0.14 * cn * exp(-r / (0.050 * pulse)));
    col += vec3(0.55, 0.78, 1.0) * 0.10 * cn * exp(-r / (0.11 * pulse));       // голубоватая дымка вокруг

    if (a > -0.03) {
        float aa = max(a, 0.0);
        float ramp = smoothstep(0.0, 0.04, aa);
        float wob = 0.6 + 1.0 * u_cbass;                          // на басах хвост подрагивает сильнее
        // газовый хвост: волокна из шума, края рваные и тают плавно
        vec2 q = vec2(b * 9.0, aa * 7.0 - u_time * 0.9);
        float warp = fbm2(q * vec2(1.0, 0.6) + 3.1) - 0.5;
        float bg = b + 0.0022 * sin(aa * 20.0 - u_time * 1.6) * (0.3 + 1.6 * aa) * wob + warp * (0.004 + 0.05 * aa);
        float wg = 0.003 + 0.030 * aa;
        float bn = abs(bg) / wg;
        float prof = exp(-bn * bn * 0.7) * (1.0 - smoothstep(0.9, 2.6, bn));
        float fib = fbm2(vec2(bg / wg * 1.8, aa * 22.0 - u_time * 1.1));            // продольные нити
        float clump = 0.7 + 0.6 * vnoise(vec2(aa * 7.0 - u_time * 0.8, 3.7));       // сгустки, плывущие от головы
        float dg = exp(-aa / 0.42) * prof * ramp * (0.30 + 1.15 * fib) * clump;
        col += vec3(0.36, 0.68, 1.0) * dg * 1.0;
        // пылевой хвост: мягкий, с тонкими волокнами у краёв
        float vb = dot(-V, P);
        float bc = 0.85 * vb * aa * aa;
        float wd = 0.012 + 0.150 * aa;
        float bd = b - bc + 0.004 * sin(aa * 9.0 - u_time * 0.8) * wob;
        bd += (fbm2(vec2(bd * 5.0, aa * 5.0 - u_time * 0.3) + 9.0) - 0.5) * 0.06 * aa;
        float dn = abs(bd) / wd;
        float dprof = exp(-dn * dn * 0.8) * (1.0 - smoothstep(1.1, 2.8, dn));
        float dfib = fbm2(vec2(bd / wd * 2.4 + 7.0, aa * 9.0 - u_time * 0.25));
        float dd = exp(-aa / 0.26) * dprof * ramp * (0.35 + 0.95 * dfib);
        col += vec3(1.0, 0.86, 0.60) * dd * 0.62;
    }
    return col * env;
}

float ampOf(vec4 ev) {
    if (ev.z < 0.0) return 0.0;
    return smoothstep(0.0, 4.0, ev.z) * (1.0 - smoothstep(38.0, 56.0, ev.z));
}

// насколько густое поле мелких звёзд у данной туманности
float starDens(float pal) {
    int ip = int(pal + 0.5);
    if (ip == 0) return 0.9;
    if (ip == 1) return 0.15;
    if (ip == 2) return 0.0;
    if (ip == 3) return 1.2;
    return 0.2;
}

// яркая звезда: ядро + крест из лучей; p - точка в сцене, c - центр звезды, size - радиус, rayLen - длина лучей
vec3 bigStar(vec2 p, vec2 c, float size, vec3 tint, float bright, float rayLen) {
    vec2 d = p - c;
    float r = length(d);
    float core = exp(-dot(d, d) / (size * size));
    float glow = exp(-r / (size * 7.0));
    float L = rayLen * 0.45;
    float ax = exp(-abs(d.x) / L) * exp(-abs(d.y) / (size * 0.45));
    float ay = exp(-abs(d.y) / L) * exp(-abs(d.x) / (size * 0.45));
    return tint * bright * (1.4 * core + 0.22 * glow + 0.28 * (ax + ay));
}

// именные звёзды туманности (позиции задаются в её локальных координатах и зажигаются не сразу)
vec3 specials(vec2 p, vec4 ev, float pal) {
    float amp = ampOf(ev);
    if (amp <= 0.001 || ev.z < 1.5) return vec3(0.0);
    vec2 o = ev.xy;
    float S = u_nsc;
    float tw = 0.92 + 0.08 * sin(u_time * 1.7 + ev.w);
    float tn = ev.z;
    vec3 c = vec3(0.0);
    int ip = int(pal + 0.5);
    vec3 white = vec3(1.0, 0.97, 0.94), blue = vec3(0.65, 0.78, 1.0), pink = vec3(1.0, 0.82, 0.78), orange = vec3(1.0, 0.72, 0.45);
    if (ip == 0) {
        c += bigStar(p, o + S * vec2(0.03, 0.14), 0.0050, blue, 2.0 * smoothstep(2.0, 4.0, tn), 0.12);
        c += bigStar(p, o + S * vec2(-0.40, -0.04), 0.0030, pink, 1.0 * smoothstep(3.0, 5.0, tn), 0.04);
        c += bigStar(p, o + S * vec2(-0.28, -0.14), 0.0028, white, 0.9 * smoothstep(4.0, 6.0, tn), 0.035);
        c += bigStar(p, o + S * vec2(-0.45, 0.07), 0.0028, orange, 0.9 * smoothstep(4.0, 6.0, tn), 0.035);
        c += bigStar(p, o + S * vec2(-0.20, 0.03), 0.0026, blue, 0.8 * smoothstep(5.0, 7.0, tn), 0.03);
        c += bigStar(p, o + S * vec2(0.52, 0.22), 0.0030, white, 0.9 * smoothstep(5.0, 7.0, tn), 0.04);
        c += bigStar(p, o + S * vec2(0.42, -0.20), 0.0028, pink, 0.9 * smoothstep(6.0, 8.0, tn), 0.035);
        c += bigStar(p, o + S * vec2(-0.78, 0.15), 0.0030, vec3(0.5, 1.0, 0.9), 0.9 * smoothstep(6.0, 8.0, tn), 0.04);
    } else if (ip == 1) {
        c += bigStar(p, o + S * vec2(-0.012, -0.090), 0.0025, white, 1.5 * smoothstep(1.5, 3.0, tn), 0.02);
        c += bigStar(p, o + S * vec2(0.008, -0.082), 0.0023, blue, 1.4 * smoothstep(1.5, 3.0, tn), 0.02);
        c += bigStar(p, o + S * vec2(-0.020, -0.112), 0.0022, white, 1.2 * smoothstep(1.5, 3.0, tn), 0.02);
        c += bigStar(p, o + S * vec2(0.014, -0.108), 0.0024, blue, 1.3 * smoothstep(1.5, 3.0, tn), 0.02);
        c += bigStar(p, o + S * vec2(-0.02, -0.38), 0.0045, blue, 1.8 * smoothstep(4.0, 6.0, tn), 0.09);
        c += bigStar(p, o + S * vec2(0.03, 0.33), 0.0030, white, 1.2 * smoothstep(5.0, 7.0, tn), 0.04);
        c += bigStar(p, o + S * vec2(-0.36, 0.34), 0.0030, blue, 1.0 * smoothstep(5.0, 7.0, tn), 0.04);
    } else if (ip == 2) {
        c += bigStar(p, o + S * vec2(0.30, 0.30), 0.0032, white, 1.0 * smoothstep(3.0, 5.0, tn), 0.05);
        c += bigStar(p, o + S * vec2(-0.42, -0.22), 0.0032, blue, 1.0 * smoothstep(4.0, 6.0, tn), 0.05);
        c += bigStar(p, o + S * vec2(-0.30, 0.34), 0.0028, orange, 0.9 * smoothstep(5.0, 7.0, tn), 0.04);
    } else if (ip == 3) {
        c += bigStar(p, o + S * vec2(-0.10, 0.06), 0.0035, vec3(1.0, 0.45, 0.60), 1.6 * smoothstep(3.0, 5.0, tn), 0.05);
        c += bigStar(p, o + S * vec2(0.20, -0.14), 0.0032, white, 1.1 * smoothstep(4.0, 6.0, tn), 0.05);
        c += bigStar(p, o + S * vec2(-0.45, 0.30), 0.0030, blue, 1.0 * smoothstep(5.0, 7.0, tn), 0.04);
        c += bigStar(p, o + S * vec2(0.40, 0.30), 0.0032, white, 1.1 * smoothstep(5.0, 7.0, tn), 0.045);
    } else {
        c += bigStar(p, o + S * vec2(0.62, -0.22), 0.0050, blue, 1.9 * smoothstep(3.0, 5.0, tn), 0.10);
        c += bigStar(p, o + S * vec2(-0.55, 0.30), 0.0032, white, 1.0 * smoothstep(4.0, 6.0, tn), 0.05);
        c += bigStar(p, o + S * vec2(-0.02, -0.28), 0.0030, orange, 1.1 * smoothstep(4.0, 6.0, tn), 0.04);
    }
    return c * amp * tw;
}

void main() {
    vec2 p = lensP((v_uv - 0.5) * vec2(u_aspect, 1.0));
    vec2 uvl = p / vec2(u_aspect, 1.0) + 0.5;
    vec2 px = uvl * u_res;
    float k = u_res.y / 1080.0;
    float nebL = dot(texture(u_neb, clamp(uvl, 0.0, 1.0)).rgb, vec3(0.333));
    float dens = ampOf(u_ev0) * starDens(u_pal0) + ampOf(u_ev1) * starDens(u_pal1);
    vec3 col = vec3(0.0);
    col += starLayer(px, 7.0 * k,  0.20, 0.70 * k, 0.0, 11.0, nebL) * 0.60;
    col += starLayer(px, 20.0 * k, 0.16, 1.00 * k, 0.0, 29.0, nebL) * 1.00;
    col += starLayer(px, 60.0 * k, 0.07, 1.40 * k, 0.0, 41.0, nebL) * 1.30;
    col += starLayer(px, 240.0 * k, 0.45, 2.20 * k, 1.0, 53.0, nebL) * 2.40;
    col += starLayer(px, 5.0 * k, 0.24, 0.62 * k, 0.0, 71.0, nebL) * 0.40 * dens;       // дополнительные мелкие звёзды
    col += specials(p, u_ev0, u_pal0) + specials(p, u_ev1, u_pal1);
    col += comet(p);
    f_color = vec4(col * u_starfade, 1.0);
}
"""

# Полноэкранный треугольник без буферов (по gl_VertexID)
QUAD_VS = """
#version 330
out vec2 v_uv;
void main() {
    vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
    v_uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

DOWN_FS = """
#version 330
uniform sampler2D tex;
uniform vec2 texel;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec3 c = texture(tex, v_uv + texel * vec2(-1.0, -1.0)).rgb
           + texture(tex, v_uv + texel * vec2( 1.0, -1.0)).rgb
           + texture(tex, v_uv + texel * vec2(-1.0,  1.0)).rgb
           + texture(tex, v_uv + texel * vec2( 1.0,  1.0)).rgb;
    f_color = vec4(c * 0.25, 1.0);
}
"""

UP_FS = """
#version 330
uniform sampler2D tex;
uniform vec2 texel;
uniform float weight;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec3 c = texture(tex, v_uv).rgb * 4.0;
    c += (texture(tex, v_uv + vec2(texel.x, 0.0)).rgb + texture(tex, v_uv - vec2(texel.x, 0.0)).rgb
        + texture(tex, v_uv + vec2(0.0, texel.y)).rgb + texture(tex, v_uv - vec2(0.0, texel.y)).rgb) * 2.0;
    c += texture(tex, v_uv + texel).rgb + texture(tex, v_uv - texel).rgb
       + texture(tex, v_uv + vec2(texel.x, -texel.y)).rgb + texture(tex, v_uv + vec2(-texel.x, texel.y)).rgb;
    f_color = vec4(c * (weight / 16.0), 1.0);
}
"""

COMPOSITE_FS = """
#version 330
uniform sampler2D u_scene;
uniform sampler2D u_bloom;
uniform sampler2D u_neb;
uniform float u_glow;
uniform float u_expo;
uniform float u_beat;
uniform float u_bass;
uniform float u_fade;
uniform float u_nebGain;
uniform float u_aspect;
uniform vec4  u_lens;
in vec2 v_uv;
out vec4 f_color;
void main() {
    vec2 uv = v_uv;
    vec3 sc = texture(u_scene, uv).rgb;
    vec3 bl = texture(u_bloom, uv).rgb;
    vec3 nb = texture(u_neb, uv).rgb;
    vec3 col = sc + bl * u_glow + nb * u_nebGain * (1.0 + 0.20 * u_beat + 0.10 * u_bass);

    vec2 q = uv - 0.5;
    float te = u_lens.z;
    if (te > 0.0) {
        // тень чёрной дыры, тонкое тёплое кольцо света по её краю и слабое сияние вокруг
        vec2 d = q * vec2(u_aspect, 1.0) - u_lens.xy;
        float r = length(d);
        float rs = 0.32 * te;
        col *= smoothstep(rs * 0.45, rs * 1.15, r);
        float x = (r - rs * 1.05) / (rs * 0.11);
        float dop = pow(0.5 + 0.5 * cos(atan(d.y, d.x) - 0.6), 1.5) * 0.9 + 0.08;   // одна сторона яркая, другая почти гаснет
        float ring = exp(-x * x) * 0.55 * dop;
        float halo = exp(-max(r - rs, 0.0) / (rs * 1.6)) * 0.07 * step(rs * 0.9, r) * (0.4 + dop);
        col += vec3(1.0, 0.84, 0.58) * (ring + halo) * (0.55 + 0.45 * u_bass) * u_lens.w;
    }
    col *= 1.0 - 0.7 * dot(q, q);                 // виньетка
    col *= u_expo * u_fade;
    col = vec3(1.0) - exp(-col);                  // мягкое сжатие ярких мест
    col = pow(max(col, vec3(0.0)), vec3(0.9));
    f_color = vec4(col, 1.0);
}
"""


def _set(prog, name, value):
    """Задать uniform, если он есть в шейдере (неиспользуемые убираются компилятором)."""
    try:
        prog[name].value = value
    except KeyError:
        pass


# ---------------------------------------------------------------------------
# Рендерер
# ---------------------------------------------------------------------------
class CosmosRenderer:
    LEVELS = 4

    def __init__(self, ctx, width, height, num_particles, scale=1.0):
        self.ctx = ctx
        self.w, self.h = width, height
        self.n = int(num_particles)
        self.aspect = width / float(height)
        self.nscale = 1.0          # масштаб композиции туманности под размер монитора
        self.sw = max(2, int(width * scale))
        self.sh = max(2, int(height * scale))

        self.prog_p = ctx.program(vertex_shader=PARTICLE_VS.replace("__COMMON__", COMMON_GLSL), fragment_shader=PARTICLE_FS)
        self.prog_neb = ctx.program(vertex_shader=QUAD_VS, fragment_shader=NEBULA_FS.replace("__COMMON__", COMMON_GLSL))
        self.prog_stars = ctx.program(vertex_shader=QUAD_VS, fragment_shader=STARS_FS.replace("__COMMON__", COMMON_GLSL))
        self.prog_down = ctx.program(vertex_shader=QUAD_VS, fragment_shader=DOWN_FS)
        self.prog_up = ctx.program(vertex_shader=QUAD_VS, fragment_shader=UP_FS)
        self.prog_comp = ctx.program(vertex_shader=QUAD_VS, fragment_shader=COMPOSITE_FS)
        self.vao_p = ctx.vertex_array(self.prog_p, [])
        self.vao_neb = ctx.vertex_array(self.prog_neb, [])
        self.vao_stars = ctx.vertex_array(self.prog_stars, [])
        self.vao_down = ctx.vertex_array(self.prog_down, [])
        self.vao_up = ctx.vertex_array(self.prog_up, [])
        self.vao_comp = ctx.vertex_array(self.prog_comp, [])

        # газ считается в уменьшенном разрешении: он мягкий, а это в разы дешевле
        self.nw = max(2, self.sw // NEB_DIV)
        self.nh = max(2, self.sh // NEB_DIV)
        self.neb_tex = self._make_tex(self.nw, self.nh)
        self.neb_fbo = ctx.framebuffer(color_attachments=[self.neb_tex])

        self.scene_tex = self._make_tex(self.sw, self.sh)
        self.scene_fbo = ctx.framebuffer(color_attachments=[self.scene_tex])

        self.levels = []  # (tex, fbo, w, h): каждый следующий в 2 раза меньше
        lw, lh = self.sw, self.sh
        for _ in range(self.LEVELS):
            lw, lh = max(1, lw // 2), max(1, lh // 2)
            tex = self._make_tex(lw, lh)
            self.levels.append((tex, ctx.framebuffer(color_attachments=[tex]), lw, lh))

    def _make_tex(self, w, h):
        tex = self.ctx.texture((w, h), 4, dtype="f2")
        tex.filter = (moderngl.LINEAR, moderngl.LINEAR)
        tex.repeat_x = False
        tex.repeat_y = False
        return tex

    def draw(self, tg, now, ev0, ev1, pal0, pal1, beat, bass, glow, fade=1.0, lens=(0.0, 0.0, 0.0, 0.0),
             comet=((0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 1.0, 0.0))):
        ctx = self.ctx
        beat = beat * BEAT_STRENGTH

        # 1. газ туманностей (в малом разрешении)
        ctx.disable(moderngl.BLEND)
        self.neb_fbo.use()
        n = self.prog_neb
        _set(n, "u_aspect", float(self.aspect))
        _set(n, "u_nsc", float(self.nscale))
        _set(n, "u_tg", float(tg))
        _set(n, "u_ev0", tuple(float(x) for x in ev0))
        _set(n, "u_ev1", tuple(float(x) for x in ev1))
        _set(n, "u_pal0", float(pal0))
        _set(n, "u_pal1", float(pal1))
        _set(n, "u_lens", tuple(float(x) for x in lens))
        self.vao_neb.render(moderngl.TRIANGLES, vertices=3)

        # 2. звёзды (читают яркость газа, чтобы в туманности быть чуть ярче)
        self.scene_fbo.use()
        self.neb_tex.use(location=0)
        s = self.prog_stars
        _set(s, "u_res", (float(self.sw), float(self.sh)))
        _set(s, "u_aspect", float(self.aspect))
        _set(s, "u_nsc", float(self.nscale))
        _set(s, "u_ev0", tuple(float(x) for x in ev0))
        _set(s, "u_ev1", tuple(float(x) for x in ev1))
        _set(s, "u_pal0", float(pal0))
        _set(s, "u_pal1", float(pal1))
        _set(s, "u_time", float(now))
        _set(s, "u_cA", tuple(float(x) for x in comet[0]))
        _set(s, "u_cB", tuple(float(x) for x in comet[1]))
        _set(s, "u_cbass", float(bass))
        _set(s, "u_lens", tuple(float(x) for x in lens))
        _set(s, "u_starfade", float(min(1.0, now / 4.0)))
        _set(s, "u_neb", 0)
        self.vao_stars.render(moderngl.TRIANGLES, vertices=3)

        # 3. частицы взрыва поверх (аддитивно)
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE
        p = self.prog_p
        _set(p, "u_res", (float(self.sw), float(self.sh)))
        _set(p, "u_aspect", float(self.aspect))
        _set(p, "u_ev0", tuple(float(x) for x in ev0))
        _set(p, "u_ev1", tuple(float(x) for x in ev1))
        _set(p, "u_pal0", float(pal0))
        _set(p, "u_pal1", float(pal1))
        _set(p, "u_beat", float(beat))
        _set(p, "u_intensity", float(INTENSITY))
        self.vao_p.render(moderngl.TRIANGLES, vertices=self.n * 6)

        # 4. свечение: цепочка уменьшения
        ctx.disable(moderngl.BLEND)
        src, sw, sh = self.scene_tex, self.sw, self.sh
        for tex, fbo, w, h in self.levels:
            fbo.use()
            src.use(location=0)
            _set(self.prog_down, "tex", 0)
            _set(self.prog_down, "texel", (1.0 / sw, 1.0 / sh))
            self.vao_down.render(moderngl.TRIANGLES, vertices=3)
            src, sw, sh = tex, w, h

        # 5. цепочка увеличения: размытые уровни складываются вверх
        ctx.enable(moderngl.BLEND)
        ctx.blend_func = moderngl.ONE, moderngl.ONE
        for i in range(len(self.levels) - 1, 0, -1):
            tex_lo, _, lw, lh = self.levels[i]
            fbo_hi = self.levels[i - 1][1]
            fbo_hi.use()
            tex_lo.use(location=0)
            _set(self.prog_up, "tex", 0)
            _set(self.prog_up, "texel", (1.0 / lw, 1.0 / lh))
            _set(self.prog_up, "weight", 1.0)
            self.vao_up.render(moderngl.TRIANGLES, vertices=3)
        ctx.disable(moderngl.BLEND)

        # 6. итоговая картинка на экран
        ctx.screen.use()
        self.scene_tex.use(location=0)
        self.levels[0][0].use(location=1)
        self.neb_tex.use(location=2)
        c = self.prog_comp
        _set(c, "u_scene", 0)
        _set(c, "u_bloom", 1)
        _set(c, "u_neb", 2)
        _set(c, "u_glow", float(1.2 * glow))
        _set(c, "u_expo", float(EXPOSURE))
        _set(c, "u_beat", float(beat))
        _set(c, "u_bass", float(bass))
        _set(c, "u_fade", float(fade))
        _set(c, "u_nebGain", float(NEB_GAIN))
        _set(c, "u_aspect", float(self.aspect))
        _set(c, "u_lens", tuple(float(x) for x in lens))
        self.vao_comp.render(moderngl.TRIANGLES, vertices=3)


def get_monitor_rects():
    """Прямоугольники всех мониторов (в пикселях виртуального экрана), слева направо."""
    rects = []
    try:
        proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC,
                                       ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

        def _cb(hmon, hdc, lprc, lparam):
            r = lprc.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return True

        user32.EnumDisplayMonitors(None, None, proc_type(_cb), 0)
    except Exception:
        logging.exception("Не удалось получить список мониторов")
    rects = [r for r in rects if r[2] > r[0] and r[3] > r[1]]
    return sorted(rects)


def monitor_layout(aspect):
    """Центры мониторов в координатах сцены (единица = высота экрана) и ширина каждого.
    Нужно, чтобы туманности рождались внутри одного монитора, а не на стыке между ними."""
    mons = []
    for (l, t, r, b) in get_monitor_rects():
        cx = ((l + r) / 2.0 - VIR_X) / float(VIR_W)
        cy = ((t + b) / 2.0 - VIR_Y) / float(VIR_H)
        mons.append(((cx - 0.5) * aspect, 0.5 - cy, (r - l) / float(VIR_H)))
    return mons if len(mons) >= 2 else []


def make_event(k, aspect, start, mons=()):
    """Параметры k-й туманности: место, зерно узора и палитра (палитры идут по кругу).
    При нескольких мониторах туманности чередуются по мониторам и держатся подальше от стыка."""
    rng = np.random.RandomState((k * 7919 + start * 131 + 13) % (2 ** 31 - 1))
    if len(mons) >= 2:
        mx, my, mw = mons[(k + start) % len(mons)]
        cx = mx + rng.uniform(-0.07, 0.07) * mw
        cy = my + rng.uniform(-0.14, 0.14)
    else:
        cx = rng.uniform(-0.28, 0.28) * aspect
        cy = rng.uniform(-0.14, 0.14)
    seed = rng.uniform(0.0, 40.0)
    return cx, cy, seed, (start + k) % 5


def slot_states(T, tc, aspect, start, mons=()):
    """Две туманности живут одновременно: новая рождается, пока старая растворяется.
    Чётные события занимают слот 0, нечётные - слот 1."""
    n = int(T // tc)
    out = []
    for s in (0, 1):
        k = n if (n % 2 == s) else n - 1
        if k < 0:
            out.append(((0.0, 0.0, -1.0, 0.0), 0.0))
        else:
            cx, cy, seed, pal = make_event(k, aspect, start, mons)
            tn = (T - k * tc) * (45.0 / tc)    # возраст в "расчётных" секундах (цикл рассчитан на 45 с)
            out.append(((cx, cy, tn, seed), float(pal)))
    return out


def lens_state(T, every, strength, aspect, nscale, mons=(), manual=None):
    """Чёрная дыра: раз в `every` секунд медленно пересекает один из мониторов.
    Возвращает (x, y, радиус кольца, видимость); радиус 0 - дыры сейчас нет.
    manual - момент, когда её запустили вручную (клавиша H в окне)."""
    if strength <= 0.0:
        return (0.0, 0.0, 0.0, 0.0)
    if manual is not None and 0.0 <= T - manual <= LENS_LIFE:
        j, t = 1000 + int(manual), T - manual
    else:
        if T < LENS_FIRST:
            return (0.0, 0.0, 0.0, 0.0)
        j = int((T - LENS_FIRST) // every)
        t = (T - LENS_FIRST) - j * every
        if t > LENS_LIFE:
            return (0.0, 0.0, 0.0, 0.0)
    rng = np.random.RandomState((j * 104729 + 7) % (2 ** 31 - 1))
    if len(mons) >= 2:
        mx, my, mw = mons[(j + rng.randint(len(mons))) % len(mons)]
    else:
        mx, my, mw = 0.0, 0.0, aspect
    half = 0.36 * mw
    x0, x1 = (-half, half) if rng.rand() < 0.5 else (half, -half)
    y0, y1 = rng.uniform(-0.20, 0.20), rng.uniform(-0.20, 0.20)
    f = t / LENS_LIFE
    f = f * f * (3.0 - 2.0 * f) * 0.35 + f * 0.65     # чуть медленнее на краях пути
    cx = mx + x0 + (x1 - x0) * f
    cy = my + y0 + (y1 - y0) * f + 0.03 * math.sin(t * 0.21)
    e = lambda a, b, v: min(1.0, max(0.0, (v - a) / (b - a)))
    env = e(0.0, 12.0, t) * (1.0 - e(LENS_LIFE - 14.0, LENS_LIFE, t))
    env = env * env * (3.0 - 2.0 * env)
    return (cx, cy, LENS_SIZE * strength * nscale * env, env)


def comet_state(T, every, strength, aspect, nscale, mons=(), manual=None):
    """Комета: раз в `every` секунд пролетает над одним из мониторов.
    Возвращает ((x, y, видимость, масштаб), (Tx, Ty, Vx, Vy)); видимость 0 - кометы нет."""
    off = ((0.0, 0.0, 0.0, 1.0), (1.0, 0.0, 1.0, 0.0))
    if strength <= 0.0:
        return off
    if manual is not None and 0.0 <= T - manual <= COMET_LIFE:
        j, t = 2000 + int(manual), T - manual
    else:
        if T < COMET_FIRST:
            return off
        j = int((T - COMET_FIRST) // every)
        t = (T - COMET_FIRST) - j * every
        if t > COMET_LIFE:
            return off
    rng = np.random.RandomState((j * 15485863 + 11) % (2 ** 31 - 1))
    if len(mons) >= 2:
        mx, my, mw = mons[(j + rng.randint(len(mons))) % len(mons)]
    else:
        mx, my, mw = 0.0, 0.0, aspect
    half = 0.44 * mw
    x0, x1 = (-half, half) if rng.rand() < 0.5 else (half, -half)
    y0, y1 = rng.uniform(-0.28, 0.28), rng.uniform(-0.28, 0.28)
    f = t / COMET_LIFE
    cx = mx + x0 + (x1 - x0) * f
    cy = my + y0 + (y1 - y0) * f
    vx, vy = x1 - x0, y1 - y0
    vl = math.hypot(vx, vy)
    vx, vy = vx / vl, vy / vl
    # хвост смотрит "от солнца": примерно назад, с отклонением; "солнечный ветер" медленно качает его
    phi = rng.choice([-1.0, 1.0]) * rng.uniform(0.35, 0.75) + 0.10 * math.sin(t * 0.35 + j)
    c, s_ = math.cos(phi), math.sin(phi)
    bx, by = -vx, -vy
    tx, ty = bx * c - by * s_, bx * s_ + by * c
    e = lambda a, b, v: min(1.0, max(0.0, (v - a) / (b - a)))
    env = e(0.0, 0.10, f) * (1.0 - e(0.80, 1.0, f))
    env = env * env * (3.0 - 2.0 * env) * min(1.0, strength)
    return ((cx, cy, env * max(1.0, strength) ** 0.5, nscale), (tx, ty, vx, vy))


# ---------------------------------------------------------------------------
# Окно настроек (/c)
# ---------------------------------------------------------------------------
def open_settings_dialog():
    cfg = load_config()

    root = tk.Tk()
    root.title("Параметры NeonCosmos")
    root.geometry("440x480")
    root.resizable(False, False)

    style = ttk.Style(root)
    style.theme_use("clam")

    ttk.Label(root, text="Настройки скринсейвера", font=("Arial", 14, "bold")).pack(pady=10)
    frame = ttk.Frame(root, padding="15")
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Количество частиц взрыва:").grid(row=0, column=0, sticky="w", pady=8)
    part_var = tk.IntVar(value=cfg["num_particles"])
    ttk.Spinbox(frame, from_=10000, to=200000, increment=10000,
                textvariable=part_var, width=10).grid(row=0, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Скорость течения газа:").grid(row=1, column=0, sticky="w", pady=8)
    speed_var = tk.DoubleVar(value=cfg["speed"])
    ttk.Scale(frame, from_=0.3, to=3.0, variable=speed_var,
              orient="horizontal", length=140).grid(row=1, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Секунд на одну туманность:").grid(row=2, column=0, sticky="w", pady=8)
    cycle_var = tk.DoubleVar(value=cfg["cycle"])
    ttk.Scale(frame, from_=20.0, to=120.0, variable=cycle_var,
              orient="horizontal", length=140).grid(row=2, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Сила свечения:").grid(row=3, column=0, sticky="w", pady=8)
    glow_var = tk.DoubleVar(value=cfg["glow"])
    ttk.Scale(frame, from_=0.3, to=2.5, variable=glow_var,
              orient="horizontal", length=140).grid(row=3, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Чёрная дыра (0 = выкл):").grid(row=4, column=0, sticky="w", pady=8)
    lens_var = tk.DoubleVar(value=cfg["lens"])
    ttk.Scale(frame, from_=0.0, to=2.0, variable=lens_var,
              orient="horizontal", length=140).grid(row=4, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Как часто (секунд):").grid(row=5, column=0, sticky="w", pady=8)
    every_var = tk.DoubleVar(value=cfg["lens_every"])
    ttk.Scale(frame, from_=60.0, to=600.0, variable=every_var,
              orient="horizontal", length=140).grid(row=5, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Кометы (0 = выкл):").grid(row=6, column=0, sticky="w", pady=8)
    comet_var = tk.DoubleVar(value=cfg["comets"])
    ttk.Scale(frame, from_=0.0, to=2.0, variable=comet_var,
              orient="horizontal", length=140).grid(row=6, column=1, sticky="e", pady=8)

    ttk.Label(frame, text="Как часто (секунд):").grid(row=7, column=0, sticky="w", pady=8)
    cevery_var = tk.DoubleVar(value=cfg["comet_every"])
    ttk.Scale(frame, from_=40.0, to=400.0, variable=cevery_var,
              orient="horizontal", length=140).grid(row=7, column=1, sticky="e", pady=8)

    def on_save():
        try:
            n = int(part_var.get())
        except Exception:
            n = DEFAULT_CONFIG["num_particles"]
        save_config({
            "num_particles": n,
            "speed": round(speed_var.get(), 2),
            "cycle": round(cycle_var.get(), 0),
            "glow": round(glow_var.get(), 2),
            "lens": round(lens_var.get(), 2),
            "lens_every": round(every_var.get(), 0),
            "comets": round(comet_var.get(), 2),
            "comet_every": round(cevery_var.get(), 0),
        })
        root.destroy()

    btn_frame = ttk.Frame(root, padding="10")
    btn_frame.pack(fill="x", side="bottom")
    ttk.Button(btn_frame, text="Сохранить", command=on_save).pack(side="right", padx=5)
    ttk.Button(btn_frame, text="Отмена", command=root.destroy).pack(side="right", padx=5)
    root.mainloop()


# ---------------------------------------------------------------------------
# Основной цикл
# ---------------------------------------------------------------------------
def make_topmost(hwnd):
    """Окно поверх всего, на все мониторы."""
    try:
        user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos(wintypes.HWND(hwnd), wintypes.HWND(-1),
                            VIR_X, VIR_Y, VIR_W, VIR_H, 0x0040)
    except Exception:
        logging.exception("Не удалось сделать окно topmost")


def save_screenshot(ctx, w, h, path):
    try:
        data = ctx.screen.read(components=3)
        surf = pygame.image.fromstring(data, (w, h), "RGB", True)
        pygame.image.save(surf, path)
        logging.info("Скриншот сохранён: %s", path)
    except Exception:
        logging.exception("Не удалось сохранить скриншот")


def run_screensaver(window=False, shots=False):
    cfg = load_config()
    audio = AudioAnalyzer()
    audio.start()

    if window:
        w = min(VIR_W, 1900)
        h = max(200, int(w * VIR_H / VIR_W))
        os.environ["SDL_VIDEO_WINDOW_POS"] = "40,60"
        flags = pygame.OPENGL | pygame.DOUBLEBUF
    else:
        w, h = VIR_W, VIR_H
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{VIR_X},{VIR_Y}"
        flags = pygame.OPENGL | pygame.DOUBLEBUF | pygame.NOFRAME

    pygame.init()
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 3)
    pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_CORE)
    try:
        pygame.display.set_mode((w, h), flags, vsync=1)
    except Exception:
        logging.exception("vsync недоступен, создаю окно без него")
        pygame.display.set_mode((w, h), flags)
    pygame.display.set_caption("NeonCosmos")

    try:
        ctx = moderngl.create_context()
        logging.info("OpenGL: %s | %s | окно %dx%d | частиц %d",
                     ctx.info.get("GL_RENDERER"), ctx.info.get("GL_VERSION"), w, h, cfg["num_particles"])
        renderer = CosmosRenderer(ctx, w, h, cfg["num_particles"], SCENE_SCALE)
    except Exception:
        logging.exception("Не удалось запустить OpenGL-рендерер")
        pygame.quit()
        sys.exit(1)

    if not window:
        pygame.mouse.set_visible(False)
        make_topmost(pygame.display.get_wm_info()["window"])

    shots_dir = None
    if shots or window:
        desktop = os.path.join(HOME, "Desktop")
        shots_dir = os.path.join(desktop if os.path.isdir(desktop) else HOME, "neon_shots")
        os.makedirs(shots_dir, exist_ok=True)
    shot_count = 0
    next_shot = 6.0

    mons = monitor_layout(renderer.aspect)
    if mons:
        renderer.nscale = max(0.5, min(1.3, min(m[2] for m in mons) / 1.78))   # композиция не шире одного монитора
        logging.info("Мониторов: %d, центры по X: %s", len(mons), [round(m[0], 2) for m in mons])
    tc = cfg["cycle"]
    start_pal = random.randrange(5)       # с какой туманности начать (дальше по кругу)
    comet_manual = None
    lens_manual = None                    # в окне клавиша H запускает чёрную дыру сразу
    t_offset = 0.0                        # в окне клавиша N перематывает к следующей туманности
    tg = 0.0
    gas_speed = 1.0

    clock = pygame.time.Clock()
    start = pygame.time.get_ticks()
    initial_mouse = None
    bass_s = vol_s = beat_s = 0.0
    frames, fps_timer, reports = 0, 0.0, 0

    running = True
    while running:
        dt = min(0.05, clock.tick(60) / 1000.0)
        elapsed = (pygame.time.get_ticks() - start) / 1000.0

        if not window and initial_mouse is None and elapsed > 1.0:
            initial_mouse = pygame.mouse.get_pos()

        want_shot = False
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if window:
                    if event.key == pygame.K_ESCAPE:
                        running = False
                    elif event.key == pygame.K_s:
                        want_shot = True
                    elif event.key == pygame.K_c:
                        comet_manual = elapsed
                    elif event.key == pygame.K_h:
                        lens_manual = elapsed
                    elif event.key == pygame.K_n:
                        T_now = elapsed + t_offset
                        t_offset += tc - (T_now % tc)
                else:
                    running = False
            elif not window and event.type == pygame.MOUSEBUTTONDOWN:
                running = False
            elif not window and event.type == pygame.MOUSEMOTION and initial_mouse is not None:
                mx, my = event.pos
                if abs(mx - initial_mouse[0]) > 30 or abs(my - initial_mouse[1]) > 30:
                    running = False

        # звук: плавное включение после AUDIO_DELAY, при тишине (нет данных) уровень = 0
        gain = min(1.0, max(0.0, (elapsed - AUDIO_DELAY) / 1.0))
        alive = (time.monotonic() - audio.last_update) < 0.3
        bass_s = smooth(bass_s, (audio.bass if alive else 0.0) * gain)
        vol_s = smooth(vol_s, (audio.volume if alive else 0.0) * gain)
        beat_raw = audio.beat if alive else 0.0
        audio.beat = 0.0
        beat_s = smooth(beat_s, beat_raw * gain, attack=0.35, release=0.10)

        # газ течёт чуть быстрее на музыке, плавно (с инерцией); ход рождения/смены туманностей музыка не трогает
        gas_target = 1.0 + 0.5 * vol_s + 0.8 * beat_s * BEAT_STRENGTH
        gas_speed = smooth(gas_speed, gas_target, attack=0.10, release=0.04)
        tg = (tg + dt * cfg["speed"] * gas_speed) % 3000.0

        T = elapsed + t_offset
        (ev0, pal0), (ev1, pal1) = slot_states(T, tc, renderer.aspect, start_pal, mons)
        fade = min(1.0, elapsed / 2.5)

        lens = lens_state(elapsed, cfg["lens_every"], cfg["lens"], renderer.aspect, renderer.nscale, mons, lens_manual)
        comet = comet_state(elapsed, cfg["comet_every"], cfg["comets"], renderer.aspect, renderer.nscale, mons, comet_manual)
        renderer.draw(tg, elapsed, ev0, ev1, pal0, pal1, beat_s, bass_s, cfg["glow"], fade, lens, comet)

        if shots_dir is not None and (want_shot or (shots and shot_count < 12 and elapsed >= next_shot)):
            shot_count += 1
            next_shot = elapsed + 6.0
            save_screenshot(ctx, w, h, os.path.join(shots_dir, f"shot_{shot_count:02d}.png"))

        pygame.display.flip()

        frames += 1
        fps_timer += dt
        if fps_timer >= 5.0:
            fps = frames / fps_timer
            if reports < 6:
                logging.info("FPS: %.1f | bass %.2f beat %.2f", fps, bass_s, beat_s)
            if window:
                pygame.display.set_caption(f"NeonCosmos  fps {fps:.0f}  туманность {int(T // tc) + 1}   [S = скриншот, N = следующая, H = чёрная дыра, C = комета, Esc = выход]")
            reports += 1
            frames, fps_timer = 0, 0.0

    audio.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    args = [a.lower() for a in sys.argv[1:]]
    flags_ = {a.strip("/-") for a in args}
    try:
        if any(a.startswith(("/c", "-c")) for a in args):
            open_settings_dialog()
        elif any(a.startswith(("/p", "-p")) for a in args):
            sys.exit()
        else:  # /s, window, shots или без аргументов
            window = "window" in flags_ or "w" in flags_
            if not window and already_running():
                sys.exit()
            run_screensaver(window=window, shots="shots" in flags_)
    except SystemExit:
        raise
    except Exception:
        logging.exception("Критическая ошибка")
        raise
