import { Fragment, useState } from "react";

// Baseline values are a common community-tested starting point for
// balanced 2-finger + gyro play. Everything else is a multiplier on top.
const BASE = {
  general: 95, redDot: 85, x2: 65, x4: 45, sniper: 20, freeLook: 90,
  gyroGeneral: 220, gyroRedDot: 200, gyroX2: 160, gyroX4: 110, gyroSniper: 70, gyroFreeLook: 180,
};

const LAYOUT_MULT = {
  2: { touch: 1.0, gyro: 1.0 },
  3: { touch: 0.95, gyro: 1.1 },
  4: { touch: 0.9, gyro: 1.2 },
};

const STYLE_MULT = {
  aggressive: { general: 1.1, redDot: 1.1, x2: 1.0, x4: 0.95, sniper: 0.9, freeLook: 1.05 },
  balanced: { general: 1.0, redDot: 1.0, x2: 1.0, x4: 1.0, sniper: 1.0, freeLook: 1.0 },
  sniper: { general: 0.95, redDot: 0.95, x2: 0.85, x4: 0.8, sniper: 0.75, freeLook: 0.95 },
};

// Screen aspect ratio is used as a stand-in for device shape, since there is
// no reliable public database mapping every phone model to its optimal FF
// sensitivity. Taller screens give more thumb travel room; tablets need
// more physical movement for the same look angle.
const ASPECT_MULT = {
  standard: 1.0,
  tall: 1.05,
  tablet: 0.9,
};

const FIELDS = [
  ["general", "gyroGeneral", "General"],
  ["redDot", "gyroRedDot", "Red Dot"],
  ["x2", "gyroX2", "2x Scope"],
  ["x4", "gyroX4", "4x Scope"],
  ["sniper", "gyroSniper", "Sniper (AWM)"],
  ["freeLook", "gyroFreeLook", "Free Look"],
];

function clamp(v, min, max) {
  return Math.round(Math.min(max, Math.max(min, v)));
}

export function calcSensitivity({ layout, gyro, style, aspect }) {
  const l = LAYOUT_MULT[layout];
  const s = STYLE_MULT[style];
  const a = ASPECT_MULT[aspect];
  const result = {};
  for (const [touchKey, gyroKey] of FIELDS) {
    const touchVal = clamp(BASE[touchKey] * s[touchKey] * l.touch * a, 1, 200);
    result[touchKey] = touchVal;
    if (gyro) {
      result[gyroKey] = clamp(BASE[gyroKey] * s[touchKey] * l.gyro * a, 1, 400);
    } else {
      result[gyroKey] = null;
    }
  }
  return result;
}

export default function SensCalculator({ lang }) {
  const [layout, setLayout] = useState(2);
  const [gyro, setGyro] = useState(true);
  const [style, setStyle] = useState("balanced");
  const [aspect, setAspect] = useState("standard");
  const [device, setDevice] = useState("");

  const result = calcSensitivity({ layout, gyro, style, aspect });

  const T = {
    title: lang === "tj" ? "Ҳисобкунаки сензитивият" : "Калькулятор чувствительности",
    subtitle: lang === "tj"
      ? "Барои Free Fire — дар асоси тарзи идоракунӣ ва услуби бозии шумо"
      : "Для Free Fire — на основе вашего управления и стиля игры",
    device: lang === "tj" ? "Модели телефон (ихтиёрӣ)" : "Модель телефона (необязательно)",
    devicePlaceholder: lang === "tj" ? "мас. Redmi Note 12" : "напр. Redmi Note 12",
    layout: lang === "tj" ? "Тарзи идоракунӣ" : "Управление",
    gyro: lang === "tj" ? "Гироскоп" : "Гироскоп",
    gyroOn: lang === "tj" ? "Фаъол" : "Вкл",
    gyroOff: lang === "tj" ? "Ғайрифаъол" : "Выкл",
    style: lang === "tj" ? "Услуби бозӣ" : "Стиль игры",
    styleAggressive: lang === "tj" ? "Ҳамлаовар" : "Агрессивный",
    styleBalanced: lang === "tj" ? "Мутавозин" : "Баланс",
    styleSniper: lang === "tj" ? "Снайпер" : "Снайпер",
    aspect: lang === "tj" ? "Намуди экран" : "Тип экрана",
    aspectStandard: lang === "tj" ? "Стандартӣ" : "Стандартный",
    aspectTall: lang === "tj" ? "Дароз (20:9+)" : "Вытянутый (20:9+)",
    aspectTablet: lang === "tj" ? "Планшет" : "Планшет",
    result: lang === "tj" ? "Танзимоти тавсияшуда" : "Рекомендуемые настройки",
    touch: lang === "tj" ? "Даст" : "Тач",
    gyroCol: "Gyro",
    tip: lang === "tj"
      ? "Ин нуқтаи оғоз аст. Дар Training Ground 10-15 патрон занед — агар силоҳ аз нишон \"давад\", гироскопро зиёд кунед; агар тез ҳаракат кунад, кам кунед."
      : "Это отправная точка. Проверьте в Training Ground — если оружие \"убегает\" от цели, увеличьте гироскоп; если двигается слишком резко, уменьшите.",
    off: lang === "tj" ? "хомӯш" : "выкл",
  };

  const pillStyle = (active) => ({
    padding: "8px 14px",
    borderRadius: 20,
    border: active ? "1px solid #00f5ff66" : "1px solid #ffffff0d",
    cursor: "pointer",
    fontSize: 12,
    fontWeight: 700,
    background: active ? "linear-gradient(135deg, #00f5ff, #0080ff)" : "#0d1220",
    color: active ? "#000" : "#888",
    transition: "all 0.2s",
  });

  const card = {
    background: "#0d1220",
    border: "1px solid #ffffff0d",
    borderRadius: 16,
    padding: "16px",
    marginBottom: 12,
  };

  const label = {
    fontSize: 11,
    fontWeight: 800,
    color: "#555",
    letterSpacing: 1,
    marginBottom: 10,
    textTransform: "uppercase",
  };

  return (
    <div style={{ padding: "20px 16px 40px" }}>
      <div style={{ fontSize: 22, fontWeight: 900, color: "#bf5af2", marginBottom: 4 }}>
        {T.title}
      </div>
      <div style={{ fontSize: 12, color: "#666", marginBottom: 20 }}>{T.subtitle}</div>

      <div style={card}>
        <div style={label}>{T.device}</div>
        <input
          style={{
            width: "100%", boxSizing: "border-box",
            background: "#141a2e", border: "1px solid #ffffff11",
            borderRadius: 10, padding: "12px 14px",
            color: "#e8eaf6", fontSize: 14, outline: "none",
            fontFamily: "inherit",
          }}
          placeholder={T.devicePlaceholder}
          value={device}
          onChange={(e) => setDevice(e.target.value)}
        />
      </div>

      <div style={card}>
        <div style={label}>{T.layout}</div>
        <div style={{ display: "flex", gap: 8 }}>
          {[2, 3, 4].map((n) => (
            <button key={n} style={pillStyle(layout === n)} onClick={() => setLayout(n)}>
              {n} {lang === "tj" ? "ангушт" : "пальца"}
            </button>
          ))}
        </div>
      </div>

      <div style={card}>
        <div style={label}>{T.gyro}</div>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={pillStyle(gyro)} onClick={() => setGyro(true)}>{T.gyroOn}</button>
          <button style={pillStyle(!gyro)} onClick={() => setGyro(false)}>{T.gyroOff}</button>
        </div>
      </div>

      <div style={card}>
        <div style={label}>{T.style}</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button style={pillStyle(style === "aggressive")} onClick={() => setStyle("aggressive")}>{T.styleAggressive}</button>
          <button style={pillStyle(style === "balanced")} onClick={() => setStyle("balanced")}>{T.styleBalanced}</button>
          <button style={pillStyle(style === "sniper")} onClick={() => setStyle("sniper")}>{T.styleSniper}</button>
        </div>
      </div>

      <div style={card}>
        <div style={label}>{T.aspect}</div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button style={pillStyle(aspect === "standard")} onClick={() => setAspect("standard")}>{T.aspectStandard}</button>
          <button style={pillStyle(aspect === "tall")} onClick={() => setAspect("tall")}>{T.aspectTall}</button>
          <button style={pillStyle(aspect === "tablet")} onClick={() => setAspect("tablet")}>{T.aspectTablet}</button>
        </div>
      </div>

      <div style={{ ...card, border: "1px solid #00f5ff33" }}>
        <div style={{ ...label, color: "#00f5ff" }}>{T.result}{device ? ` — ${device}` : ""}</div>
        <div style={{ display: "grid", gridTemplateColumns: "1.2fr 0.9fr 0.9fr", gap: "8px 4px", fontSize: 13 }}>
          <div style={{ color: "#555", fontWeight: 700, fontSize: 11 }} />
          <div style={{ color: "#555", fontWeight: 700, fontSize: 11, textAlign: "right" }}>{T.touch}</div>
          <div style={{ color: "#555", fontWeight: 700, fontSize: 11, textAlign: "right" }}>{T.gyroCol}</div>
          {FIELDS.map(([touchKey, gyroKey, name]) => (
            <Fragment key={touchKey}>
              <div style={{ color: "#aaa" }}>{name}</div>
              <div style={{ color: "#00f5ff", fontWeight: 800, textAlign: "right" }}>
                {result[touchKey]}
              </div>
              <div style={{ color: gyro ? "#bf5af2" : "#444", fontWeight: 800, textAlign: "right" }}>
                {gyro ? result[gyroKey] : T.off}
              </div>
            </Fragment>
          ))}
        </div>
      </div>

      <div style={{ fontSize: 12, color: "#666", lineHeight: 1.5, padding: "4px 4px" }}>
        💡 {T.tip}
      </div>
    </div>
  );
}
