# game-donat-bot
import { useState } from "react";

const translations = {
  tj: {
    appName: "GAME DONAT",
    tagline: "Донати бозиҳо — зуд ва осон",
    games: "Бозиҳо",
    giftCode: "Gift Code",
    profile: "Профил",
    history: "Таърих",
    settings: "Танзимот",
    balance: "Баланс",
    totalSpent: "Ҷамъи харочот",
    topUp: "Пур кардани баланс",
    inviteFriend: "Даъват кардани дӯстон",
    notifications: "Огоҳиҳо",
    support: "Дастгирй",
    language: "Забон",
    darkMode: "Реҷаи шабона",
    selectId: "ID-И БОЗИНГАР",
    enterId: "ID-ро ворид кунед",
    verify: "Санҷидан",
    selectRegion: "Минтақаро интихоб кунед",
    selectProduct: "МАҲСУЛОТРО ИНТИХОБ КУНЕД",
    diamonds: "Алмосҳо",
    passes: "Иҷозатномаҳо",
    selectPackage: "БАСТАРО ИНТИХОБ КУНЕД",
    buyNow: "ХАРИДАН →",
    somoni: "сомонй",
    orders: "Фармоишҳо",
    finance: "Молия",
    income: "Ҷамъи даромад",
    expense: "Ҷамъи харочот",
    noOrders: "Фармоишҳо нест",
    noData: "Маълумот нест",
    all: "Ҳама",
    completed: "Ичро шуд",
    pending: "Дар интизор",
    cancelled: "Бекор шуд",
    promoCode: "Рамзи промо",
    apply: "Татбиқ",
    bonusCodes: "Рамзҳои бонус 🎁",
    activeBonuses: "Рамзҳои фаъоли бонусатон:",
    firstPurchase: "Барои харидани аввал",
    close: "Пӯшидан",
    topUpAmount: "МИҚДОРРО ИНТИХОБ КУНЕД",
    continue: "Идома додан →",
    payMethod: "УСУЛИ ПАРДОХТРО ИНТИХОБ КУНЕД",
    welcome: "Хуш омадед",
  },
  ru: {
    appName: "GAME DONAT",
    tagline: "Донат игр — быстро и легко",
    games: "Игры",
    giftCode: "Gift Code",
    profile: "Профиль",
    history: "История",
    settings: "Настройки",
    balance: "Баланс",
    totalSpent: "Всего потрачено",
    topUp: "Пополнить баланс",
    inviteFriend: "Пригласить друга",
    notifications: "Уведомления",
    support: "Поддержка",
    language: "Язык",
    darkMode: "Ночной режим",
    selectId: "ID ИГРОКА",
    enterId: "Введите ID",
    verify: "Проверить",
    selectRegion: "Выберите регион",
    selectProduct: "ВЫБЕРИТЕ ПРОДУКТ",
    diamonds: "Алмазы",
    passes: "Пропуски",
    selectPackage: "ВЫБЕРИТЕ ПАКЕТ",
    buyNow: "КУПИТЬ →",
    somoni: "сомони",
    orders: "Заказы",
    finance: "Финансы",
    income: "Общий доход",
    expense: "Общий расход",
    noOrders: "Нет заказов",
    noData: "Нет данных",
    all: "Все",
    completed: "Выполнен",
    pending: "В ожидании",
    cancelled: "Отменён",
    promoCode: "Промо-код",
    apply: "Применить",
    bonusCodes: "Бонус-коды 🎁",
    activeBonuses: "Ваши активные бонус-коды:",
    firstPurchase: "На первую покупку",
    close: "Закрыть",
    topUpAmount: "ВЫБЕРИТЕ СУММУ",
    continue: "Продолжить →",
    payMethod: "ВЫБЕРИТЕ МЕТОД ОПЛАТЫ",
    welcome: "Добро пожаловать",
  },
};

const GAMES = [
  {
    id: "pubg",
    name: "PUBG MOBILE",
    sub: "UC",
    emoji: "🎯",
    color: "#f5a623",
    glow: "#f5a62366",
    badge: "TOP",
    region: true,
    tabs: ["UC", "Тўпламҳо"],
    products: {
      UC: [
        { id: 1, name: "60 UC", price: 10 },
        { id: 2, name: "120 UC", price: 21 },
        { id: 3, name: "325 UC", price: 45 },
        { id: 4, name: "660 UC", price: 89 },
        { id: 5, name: "1800 UC", price: 220 },
        { id: 6, name: "3850 UC", price: 418 },
        { id: 7, name: "8100 UC", price: 838 },
      ],
    },
  },
  {
    id: "freefire",
    name: "FREE FIRE",
    sub: "Diamonds & Pass",
    emoji: "🔥",
    color: "#ff4d4d",
    glow: "#ff4d4d55",
    badge: "HOT",
    region: true,
    tabs: ["Алмосҳо", "Иҷозатномаҳо"],
    products: {
      "Алмосҳо": [
        { id: 1, name: "110 💎", price: 8.9 },
        { id: 2, name: "341 💎", price: 27.9 },
        { id: 3, name: "572 💎", price: 46.9 },
        { id: 4, name: "1166 💎", price: 95.9 },
        { id: 5, name: "2398 💎", price: 199.9 },
        { id: 6, name: "6160 💎", price: 450 },
      ],
      "Иҷозатномаҳо": [
        { id: 1, name: "6 Level Up", price: 4 },
        { id: 2, name: "10 Level Up", price: 7 },
        { id: 3, name: "15 Level Up", price: 7 },
        { id: 4, name: "20 Level Up", price: 7 },
        { id: 5, name: "25 Level Up", price: 7 },
        { id: 6, name: "30 Level Up", price: 19 },
      ],
    },
  },
  {
    id: "ml",
    name: "MOBILE LEGENDS",
    sub: "Diamonds",
    emoji: "⚔️",
    color: "#00d4ff",
    glow: "#00d4ff44",
    badge: "GLOBAL",
    region: false,
    tabs: ["Алмосҳо"],
    products: {
      "Алмосҳо": [
        { id: 1, name: "50 💎", price: 7 },
        { id: 2, name: "100 💎", price: 14 },
        { id: 3, name: "250 💎", price: 34 },
        { id: 4, name: "500 💎", price: 67 },
        { id: 5, name: "1000 💎", price: 133 },
        { id: 6, name: "2000 💎", price: 260 },
      ],
    },
  },
  {
    id: "genshin",
    name: "GENSHIN IMPACT",
    sub: "Genesis Crystals",
    emoji: "✨",
    color: "#a78bfa",
    glow: "#a78bfa44",
    badge: "GLOBAL",
    region: false,
    tabs: ["Кристаллҳо"],
    products: {
      "Кристаллҳо": [
        { id: 1, name: "60 кристалл", price: 9 },
        { id: 2, name: "300 кристалл", price: 43 },
        { id: 3, name: "980 кристалл", price: 140 },
        { id: 4, name: "1980 кристалл", price: 280 },
        { id: 5, name: "3280 кристалл", price: 450 },
        { id: 6, name: "6480 кристалл", price: 880 },
      ],
    },
  },
  {
    id: "delta",
    name: "DELTA FORCE",
    sub: "Coins",
    emoji: "🪖",
    color: "#34d399",
    glow: "#34d39944",
    badge: "NEW",
    region: false,
    tabs: ["Монета"],
    products: {
      "Монета": [
        { id: 1, name: "100 монета", price: 8 },
        { id: 2, name: "300 монета", price: 22 },
        { id: 3, name: "980 монета", price: 68 },
        { id: 4, name: "1980 монета", price: 135 },
        { id: 5, name: "3280 монета", price: 220 },
        { id: 6, name: "6480 монета", price: 430 },
      ],
    },
  },
];

const REGIONS = [
  { code: "GLOBAL", label: "Global", flag: "🌐" },
  { code: "IN", label: "Indonesia", flag: "🇮🇩" },
  { code: "BR", label: "Brazil", flag: "🇧🇷" },
  { code: "TW", label: "Taiwan", flag: "🇹🇼" },
];

const TOP_UP_AMOUNTS = [10, 20, 50, 100, 200, 500];

const PAY_METHODS = [
  { id: "dcnext_phone", name: "DC Next (Telefon)", icon: "📱", min: 10 },
  { id: "dcnext_card", name: "DC Next (Karta)", icon: "💳", min: 10 },
  { id: "alif", name: "Alif", icon: "🟢", min: 10 },
];

export default function App() {
  const [lang, setLang] = useState("tj");
  const [tab, setTab] = useState("home");
  const [selectedGame, setSelectedGame] = useState(null);
  const [playerId, setPlayerId] = useState("");
  const [region, setRegion] = useState("GLOBAL");
  const [productTab, setProductTab] = useState(0);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [historyTab, setHistoryTab] = useState("orders");
  const [historyFilter, setHistoryFilter] = useState("all");
  const [topUpStep, setTopUpStep] = useState(0);
  const [topUpAmount, setTopUpAmount] = useState(0);
  const [customAmount, setCustomAmount] = useState("");
  const [showBonus, setShowBonus] = useState(false);
  const [promoCode, setPromoCode] = useState("");

  const t = translations[lang];

  const openGame = (game) => {
    setSelectedGame(game);
    setProductTab(0);
    setSelectedProduct(null);
    setPlayerId("");
    setRegion("GLOBAL");
  };

  const goBack = () => {
    if (selectedProduct) { setSelectedProduct(null); return; }
    if (selectedGame) { setSelectedGame(null); return; }
    if (topUpStep > 0) { setTopUpStep(topUpStep - 1); return; }
  };

  const currentGame = selectedGame;
  const currentTabs = currentGame ? currentGame.tabs : [];
  const currentTabName = currentTabs[productTab] || "";
  const currentProducts = currentGame?.products[currentTabName] || [];

  // Styles
  const styles = {
    app: {
      minHeight: "100vh",
      background: "#080b14",
      color: "#e8eaf6",
      fontFamily: "'Exo 2', 'Segoe UI', sans-serif",
      maxWidth: 430,
      margin: "0 auto",
      position: "relative",
      overflowX: "hidden",
    },
    header: {
      padding: "16px 20px 12px",
      background: "linear-gradient(180deg, #0d1220 0%, transparent 100%)",
      display: "flex",
      alignItems: "center",
      justifyContent: "space-between",
      position: "sticky",
      top: 0,
      zIndex: 100,
      backdropFilter: "blur(12px)",
      borderBottom: "1px solid #ffffff0a",
    },
    logo: {
      fontSize: 20,
      fontWeight: 900,
      letterSpacing: 2,
      background: "linear-gradient(90deg, #00f5ff, #bf5af2)",
      WebkitBackgroundClip: "text",
      WebkitTextFillColor: "transparent",
    },
    langBtn: (active) => ({
      padding: "4px 10px",
      borderRadius: 8,
      border: "none",
      cursor: "pointer",
      fontSize: 12,
      fontWeight: 700,
      background: active ? "linear-gradient(135deg, #00f5ff22, #bf5af222)" : "transparent",
      color: active ? "#00f5ff" : "#666",
      border: active ? "1px solid #00f5ff44" : "1px solid transparent",
      transition: "all 0.2s",
    }),
    bottomNav: {
      position: "fixed",
      bottom: 0,
      left: "50%",
      transform: "translateX(-50%)",
      width: "100%",
      maxWidth: 430,
      background: "#0d1220ee",
      backdropFilter: "blur(20px)",
      borderTop: "1px solid #ffffff0f",
      display: "flex",
      zIndex: 200,
    },
    navItem: (active) => ({
      flex: 1,
      padding: "10px 0 14px",
      display: "flex",
      flexDirection: "column",
      alignItems: "center",
      gap: 3,
      cursor: "pointer",
      background: "none",
      border: "none",
      color: active ? "#00f5ff" : "#444",
      fontSize: 10,
      fontWeight: active ? 700 : 500,
      transition: "all 0.2s",
      letterSpacing: 0.5,
    }),
    navIcon: { fontSize: 20 },
    content: {
      padding: "0 0 80px",
      minHeight: "calc(100vh - 60px)",
    },
    gameCard: (game) => ({
      background: `linear-gradient(135deg, #0d1220, #141a2e)`,
      border: `1px solid ${game.color}33`,
      borderRadius: 16,
      padding: "16px",
      cursor: "pointer",
      position: "relative",
      overflow: "hidden",
      transition: "all 0.3s",
      boxShadow: `0 4px 24px ${game.glow}`,
    }),
    badge: (color) => ({
      position: "absolute",
      top: 10,
      right: 10,
      background: color,
      color: "#000",
      fontSize: 9,
      fontWeight: 900,
      padding: "2px 7px",
      borderRadius: 6,
      letterSpacing: 1,
    }),
    section: {
      padding: "20px 16px 0",
    },
    sectionTitle: {
      fontSize: 11,
      fontWeight: 800,
      color: "#555",
      letterSpacing: 2,
      marginBottom: 14,
      textTransform: "uppercase",
    },
    productCard: (selected) => ({
      background: selected
        ? "linear-gradient(135deg, #00f5ff15, #bf5af215)"
        : "#0d1220",
      border: selected ? "1px solid #00f5ff66" : "1px solid #ffffff0d",
      borderRadius: 12,
      padding: "14px 12px",
      cursor: "pointer",
      transition: "all 0.2s",
      display: "flex",
      justifyContent: "space-between",
      alignItems: "center",
      boxShadow: selected ? "0 0 16px #00f5ff22" : "none",
    }),
    pill: (active) => ({
      padding: "8px 16px",
      borderRadius: 20,
      border: "none",
      cursor: "pointer",
      fontSize: 13,
      fontWeight: 700,
      background: active ? "linear-gradient(135deg, #00f5ff, #0080ff)" : "#141a2e",
      color: active ? "#000" : "#666",
      transition: "all 0.2s",
      boxShadow: active ? "0 0 16px #00f5ff55" : "none",
    }),
    buyBtn: (color) => ({
      width: "100%",
      padding: "16px",
      borderRadius: 14,
      border: "none",
      cursor: "pointer",
      fontSize: 15,
      fontWeight: 900,
      background: `linear-gradient(135deg, ${color}, ${color}88)`,
      color: "#000",
      letterSpacing: 2,
      boxShadow: `0 4px 24px ${color}66`,
      transition: "all 0.2s",
      marginTop: 20,
    }),
    card: {
      background: "#0d1220",
      border: "1px solid #ffffff0d",
      borderRadius: 16,
      padding: "16px",
    },
    input: {
      flex: 1,
      background: "#141a2e",
      border: "1px solid #ffffff11",
      borderRadius: 10,
      padding: "12px 14px",
      color: "#e8eaf6",
      fontSize: 14,
      outline: "none",
      fontFamily: "inherit",
    },
    verifyBtn: {
      background: "linear-gradient(135deg, #bf5af2, #7c3aed)",
      border: "none",
      borderRadius: 10,
      padding: "12px 18px",
      color: "#fff",
      fontWeight: 800,
      cursor: "pointer",
      fontSize: 13,
      whiteSpace: "nowrap",
      boxShadow: "0 0 16px #bf5af244",
    },
    regionCard: (active) => ({
      flex: 1,
      background: active ? "linear-gradient(135deg, #00f5ff11, #0080ff11)" : "#0d1220",
      border: active ? "1px solid #00f5ff66" : "1px solid #ffffff0d",
      borderRadius: 12,
      padding: "10px 6px",
      cursor: "pointer",
      textAlign: "center",
      transition: "all 0.2s",
    }),
    profileCard: {
      background: "linear-gradient(135deg, #0d1220, #141a2e)",
      border: "1px solid #00f5ff22",
      borderRadius: 20,
      padding: "20px",
      margin: "16px",
      boxShadow: "0 4px 32px #00f5ff11",
    },
    statBox: {
      background: "#080b14",
      border: "1px solid #ffffff0d",
      borderRadius: 12,
      padding: "12px",
      flex: 1,
      textAlign: "center",
    },
    amountBtn: (active) => ({
      padding: "14px 8px",
      background: active ? "linear-gradient(135deg, #00f5ff22, #bf5af222)" : "#0d1220",
      border: active ? "1px solid #00f5ff66" : "1px solid #ffffff0d",
      borderRadius: 12,
      color: active ? "#00f5ff" : "#aaa",
      fontWeight: 800,
      fontSize: 13,
      cursor: "pointer",
      transition: "all 0.2s",
    }),
  };

  // ============ SCREENS ============

  const HomeScreen = () => (
    <div>
      {/* Hero Banner */}
      <div style={{
        margin: "0 16px 4px",
        background: "linear-gradient(135deg, #0d1220, #141a2e)",
        border: "1px solid #00f5ff22",
        borderRadius: 20,
        padding: "20px",
        position: "relative",
        overflow: "hidden",
      }}>
        <div style={{
          position: "absolute", top: -20, right: -20,
          width: 120, height: 120,
          background: "radial-gradient(circle, #00f5ff22, transparent)",
          borderRadius: "50%",
        }} />
        <div style={{ fontSize: 28, marginBottom: 6 }}>🎮</div>
        <div style={{ fontSize: 18, fontWeight: 900, marginBottom: 4 }}>
          {t.appName}
        </div>
        <div style={{ fontSize: 12, color: "#666" }}>{t.tagline}</div>
        <div style={{
          marginTop: 12,
          display: "inline-block",
          background: "linear-gradient(135deg, #00f5ff, #0080ff)",
          color: "#000",
          padding: "6px 14px",
          borderRadius: 20,
          fontSize: 11,
          fontWeight: 900,
          letterSpacing: 1,
          cursor: "pointer",
        }} onClick={() => setShowBonus(true)}>
          🎁 BONUS CODE: WELCOME
        </div>
      </div>

      {/* Games grid */}
      <div style={styles.section}>
        <div style={styles.sectionTitle}>{t.games}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {GAMES.map(game => (
            <div key={game.id} style={styles.gameCard(game)} onClick={() => openGame(game)}>
              <div style={styles.badge(game.color)}>{game.badge}</div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <div style={{
                  width: 52, height: 52,
                  background: `radial-gradient(circle, ${game.glow}, transparent)`,
                  borderRadius: 14,
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 26,
                  border: `1px solid ${game.color}33`,
                }}>
                  {game.emoji}
                </div>
                <div>
                  <div style={{ fontWeight: 900, fontSize: 15, color: game.color }}>{game.name}</div>
                  <div style={{ fontSize: 11, color: "#555", marginTop: 2 }}>{game.sub}</div>
                </div>
                <div style={{ marginLeft: "auto", color: "#333", fontSize: 18 }}>›</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );

  const GameScreen = () => {
    const game = currentGame;
    if (!game) return null;

    if (selectedProduct) {
      return (
        <div style={styles.section}>
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 11, color: "#555", letterSpacing: 2 }}>{game.name}</div>
            <div style={{ fontSize: 20, fontWeight: 900, color: game.color, marginTop: 4 }}>
              {selectedProduct.name}
            </div>
          </div>
          <div style={{ ...styles.card, marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: "#555", marginBottom: 8 }}>ID БОЗИНГАР</div>
            <div style={{ color: playerId || "#444", fontSize: 15, fontWeight: 700 }}>
              {playerId || "—"}
            </div>
          </div>
          <div style={{ ...styles.card, marginBottom: 12 }}>
            <div style={{ fontSize: 11, color: "#555", marginBottom: 4 }}>МИНТАҚА</div>
            <div style={{ color: "#aaa", fontSize: 13 }}>{region}</div>
          </div>
          <div style={{ ...styles.card }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <div style={{ fontSize: 11, color: "#555", marginBottom: 4 }}>МАҲСУЛОТ</div>
                <div style={{ fontWeight: 800, color: game.color }}>{selectedProduct.name}</div>
              </div>
              <div style={{ fontSize: 22, fontWeight: 900, color: "#00f5ff" }}>
                {selectedProduct.price} <span style={{ fontSize: 12, color: "#555" }}>{t.somoni}</span>
              </div>
            </div>
          </div>
          <button style={styles.buyBtn(game.color)}>{t.buyNow}</button>
        </div>
      );
    }

    return (
      <div>
        <div style={styles.section}>
          {/* Game header */}
          <div style={{
            display: "flex", alignItems: "center", gap: 12, marginBottom: 20,
            background: `linear-gradient(135deg, ${game.glow}, transparent)`,
            padding: "14px", borderRadius: 14,
            border: `1px solid ${game.color}33`,
          }}>
            <div style={{ fontSize: 32 }}>{game.emoji}</div>
            <div>
              <div style={{ fontWeight: 900, fontSize: 17, color: game.color }}>{game.name}</div>
              <div style={{ fontSize: 12, color: "#555" }}>{game.sub}</div>
            </div>
          </div>

          {/* Player ID */}
          <div style={{ ...styles.card, marginBottom: 12 }}>
            <div style={styles.sectionTitle}>{t.selectId}</div>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                style={styles.input}
                placeholder={t.enterId}
                value={playerId}
                onChange={e => setPlayerId(e.target.value)}
              />
              <button style={styles.verifyBtn}>{t.verify}</button>
            </div>
          </div>

          {/* Region */}
          {game.region && (
            <div style={{ ...styles.card, marginBottom: 12 }}>
              <div style={styles.sectionTitle}>{t.selectRegion}</div>
              <div style={{ display: "flex", gap: 8 }}>
                {REGIONS.map(r => (
                  <div key={r.code} style={styles.regionCard(region === r.code)} onClick={() => setRegion(r.code)}>
                    <div style={{ fontSize: 18 }}>{r.flag}</div>
                    <div style={{ fontSize: 10, fontWeight: 700, color: region === r.code ? "#00f5ff" : "#555", marginTop: 3 }}>{r.code}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Product tabs */}
          <div style={styles.sectionTitle}>{t.selectProduct}</div>
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            {currentTabs.map((tabName, i) => (
              <button key={i} style={styles.pill(productTab === i)} onClick={() => setProductTab(i)}>
                {tabName}
              </button>
            ))}
          </div>

          {/* Products grid */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {currentProducts.map(p => (
              <div key={p.id} style={styles.productCard(selectedProduct?.id === p.id)}
                onClick={() => setSelectedProduct(p)}>
                <div>
                  <div style={{ fontWeight: 800, fontSize: 14 }}>{p.name}</div>
                  <div style={{ fontSize: 12, color: game.color, marginTop: 2 }}>{p.price} {t.somoni}</div>
                </div>
                {selectedProduct?.id === p.id && <div style={{ color: "#00f5ff", fontSize: 16 }}>✓</div>}
              </div>
            ))}
          </div>

          {selectedProduct && (
            <button style={styles.buyBtn(game.color)} onClick={() => {}}>
              {t.buyNow} — {selectedProduct.price} {t.somoni}
            </button>
          )}
        </div>
      </div>
    );
  };

  const HistoryScreen = () => (
    <div style={styles.section}>
      <div style={{ fontSize: 24, fontWeight: 900, marginBottom: 16, color: "#00f5ff" }}>
        {t.history}
      </div>
      {/* Tabs */}
      <div style={{
        display: "flex",
        background: "#0d1220",
        borderRadius: 14,
        padding: 4,
        marginBottom: 16,
        border: "1px solid #ffffff0d",
      }}>
        {[["orders", t.orders], ["finance", t.finance]].map(([key, label]) => (
          <button key={key} onClick={() => setHistoryTab(key)} style={{
            flex: 1, padding: "10px", borderRadius: 10, border: "none",
            background: historyTab === key ? "linear-gradient(135deg, #00f5ff, #0080ff)" : "transparent",
            color: historyTab === key ? "#000" : "#555",
            fontWeight: 800, cursor: "pointer", fontSize: 13,
            transition: "all 0.2s",
          }}>
            {label}
          </button>
        ))}
      </div>

      {historyTab === "finance" ? (
        <>
          <div style={{ display: "flex", gap: 10, marginBottom: 16 }}>
            <div style={styles.statBox}>
              <div style={{ fontSize: 11, color: "#555", marginBottom: 6 }}>{t.income}</div>
              <div style={{ fontSize: 22, fontWeight: 900, color: "#34d399" }}>+0</div>
              <div style={{ fontSize: 11, color: "#555" }}>{t.somoni}</div>
            </div>
            <div style={styles.statBox}>
              <div style={{ fontSize: 11, color: "#555", marginBottom: 6 }}>{t.expense}</div>
              <div style={{ fontSize: 22, fontWeight: 900, color: "#f87171" }}>-0</div>
              <div style={{ fontSize: 11, color: "#555" }}>{t.somoni}</div>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            {[t.all, "↓ " + t.income.split(" ")[1], "↑ " + t.expense.split(" ")[1]].map((f, i) => (
              <button key={i} style={{
                padding: "8px 14px", borderRadius: 20, border: "none",
                background: i === 0 ? "linear-gradient(135deg, #00f5ff, #0080ff)" : "#0d1220",
                color: i === 0 ? "#000" : "#555",
                fontWeight: 700, fontSize: 12, cursor: "pointer",
                border: i !== 0 ? "1px solid #ffffff0d" : "none",
              }}>{f}</button>
            ))}
          </div>
          <div style={{ textAlign: "center", padding: "40px 0", color: "#333", fontSize: 13 }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>🕐</div>
            {t.noData}
          </div>
        </>
      ) : (
        <>
          <div style={{ display: "flex", gap: 6, marginBottom: 14, flexWrap: "wrap" }}>
            {[t.all, t.completed, t.pending, t.cancelled].map((f, i) => (
              <button key={i} onClick={() => setHistoryFilter(f)} style={{
                padding: "8px 14px", borderRadius: 20, border: "none",
                background: historyFilter === f
                  ? "linear-gradient(135deg, #00f5ff, #0080ff)"
                  : "#0d1220",
                color: historyFilter === f ? "#000" : "#555",
                fontWeight: 700, fontSize: 12, cursor: "pointer",
                border: historyFilter !== f ? "1px solid #ffffff0d" : "none",
              }}>{f}</button>
            ))}
          </div>
          <div style={{ textAlign: "center", padding: "40px 0", color: "#333", fontSize: 13 }}>
            <div style={{ fontSize: 32, marginBottom: 8 }}>🕐</div>
            {t.noOrders}
          </div>
        </>
      )}
    </div>
  );

  const TopUpScreen = () => {
    const chosen = topUpAmount || parseInt(customAmount) || 0;
    return (
      <div style={styles.section}>
        <div style={{ fontSize: 24, fontWeight: 900, marginBottom: 4, color: "#00f5ff" }}>
          {t.topUp}
        </div>
        <div style={{ fontSize: 12, color: "#555", marginBottom: 20 }}>
          ← {lang === "tj" ? "Иваз кардани маблағ" : "Изменить сумму"}
        </div>
        <div style={styles.sectionTitle}>{t.topUpAmount}</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginBottom: 14 }}>
          {TOP_UP_AMOUNTS.map(amount => (
            <button key={amount} style={styles.amountBtn(topUpAmount === amount)}
              onClick={() => { setTopUpAmount(amount); setCustomAmount(""); }}>
              {amount} {t.somoni}
            </button>
          ))}
        </div>
        <input
          style={{ ...styles.input, width: "100%", boxSizing: "border-box", marginBottom: 14 }}
          placeholder="0"
          value={customAmount}
          onChange={e => { setCustomAmount(e.target.value); setTopUpAmount(0); }}
          type="number"
        />
        <button style={styles.buyBtn("#00f5ff")} disabled={!chosen}>
          {t.continue} {chosen ? `— ${chosen} ${t.somoni}` : ""}
        </button>

        <div style={{ marginTop: 24, ...styles.sectionTitle }}>{t.payMethod}</div>
        {PAY_METHODS.map(m => (
          <div key={m.id} style={{ ...styles.productCard(false), marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{
                width: 40, height: 40, background: "#141a2e",
                borderRadius: 10, display: "flex", alignItems: "center",
                justifyContent: "center", fontSize: 20,
              }}>{m.icon}</div>
              <div>
                <div style={{ fontWeight: 700, fontSize: 14 }}>{m.name}</div>
                <div style={{ fontSize: 11, color: "#555" }}>{lang === "tj" ? "Ақалан" : "Минимум"} {m.min} {t.somoni}</div>
              </div>
            </div>
            <div style={{ color: "#333" }}>›</div>
          </div>
        ))}
      </div>
    );
  };

  const ProfileScreen = () => (
    <div>
      <div style={styles.profileCard}>
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 16 }}>
          <div style={{
            width: 56, height: 56,
            background: "linear-gradient(135deg, #00f5ff, #bf5af2)",
            borderRadius: "50%",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 24, fontWeight: 900, color: "#000",
          }}>G</div>
          <div>
            <div style={{ fontWeight: 900, fontSize: 17 }}>Guest</div>
            <div style={{ fontSize: 12, color: "#555" }}>@guest</div>
          </div>
          <div style={{ marginLeft: "auto", cursor: "pointer", fontSize: 18, color: "#333" }}>🌙</div>
        </div>
        <div style={{
          background: "linear-gradient(135deg, #00f5ff22, #bf5af222)",
          border: "1px solid #00f5ff22",
          borderRadius: 14, padding: "14px",
          marginBottom: 14,
        }}>
          <div style={{ fontSize: 11, color: "#555", marginBottom: 4 }}>{t.balance}</div>
          <div style={{ fontSize: 32, fontWeight: 900, color: "#00f5ff" }}>
            0 <span style={{ fontSize: 14, color: "#555" }}>{t.somoni}</span>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {[
            { label: t.topUp.split(" ")[0], icon: "💰" },
            { label: lang === "tj" ? "Бонус" : "Бонус", icon: "🎁" },
            { label: lang === "tj" ? "Дастгирй" : "Помощь", icon: "💬" },
          ].map((btn, i) => (
            <button key={i} onClick={i === 1 ? () => setShowBonus(true) : i === 0 ? () => setTab("topup") : undefined}
              style={{
                flex: 1, padding: "10px 4px",
                background: "#141a2e",
                border: "1px solid #ffffff0d",
                borderRadius: 12,
                color: "#aaa", fontSize: 11, fontWeight: 700,
                cursor: "pointer", display: "flex", flexDirection: "column",
                alignItems: "center", gap: 4,
              }}>
              <span style={{ fontSize: 18 }}>{btn.icon}</span>
              {btn.label}
            </button>
          ))}
        </div>
      </div>

      <div style={{ margin: "0 16px" }}>
        {/* Language */}
        <div style={{ ...styles.card, marginBottom: 10, display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 20 }}>🌐</span>
            <span style={{ fontWeight: 600 }}>{t.language}</span>
          </div>
          <div style={{ display: "flex", gap: 4 }}>
            <button style={styles.langBtn(lang === "tj")} onClick={() => setLang("tj")}>Тоҷикӣ</button>
            <button style={styles.langBtn(lang === "ru")} onClick={() => setLang("ru")}>Русский</button>
          </div>
        </div>

        {[
          { icon: "👥", label: t.inviteFriend },
          { icon: "🔔", label: t.notifications },
          { icon: "💬", label: t.support },
          { icon: "🛡️", label: lang === "tj" ? "Амнияти маълумот" : "Безопасность данных" },
        ].map((item, i) => (
          <div key={i} style={{
            ...styles.card, marginBottom: 8,
            display: "flex", alignItems: "center", justifyContent: "space-between",
            cursor: "pointer",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 20 }}>{item.icon}</span>
              <span style={{ fontWeight: 600 }}>{item.label}</span>
            </div>
            <span style={{ color: "#333" }}>›</span>
          </div>
        ))}
      </div>
    </div>
  );

  // Bonus modal
  const BonusModal = () => (
    <div style={{
      position: "fixed", inset: 0, zIndex: 500,
      background: "#000000aa",
      display: "flex", alignItems: "flex-end",
    }} onClick={() => setShowBonus(false)}>
      <div style={{
        width: "100%", maxWidth: 430, margin: "0 auto",
        background: "#0d1220",
        borderRadius: "24px 24px 0 0",
        padding: "24px 20px 32px",
        border: "1px solid #ffffff0d",
        borderBottom: "none",
      }} onClick={e => e.stopPropagation()}>
        <div style={{ width: 36, height: 4, background: "#333", borderRadius: 2, margin: "0 auto 20px" }} />
        <div style={{ fontSize: 20, fontWeight: 900, marginBottom: 6 }}>{t.bonusCodes}</div>
        <div style={{ fontSize: 13, color: "#555", marginBottom: 20 }}>{t.activeBonuses}</div>
        <div style={{
          background: "#141a2e",
          border: "1px solid #00f5ff22",
          borderRadius: 14, padding: "16px",
          display: "flex", justifyContent: "space-between", alignItems: "center",
          marginBottom: 20,
        }}>
          <div>
            <div style={{ fontSize: 12, color: "#555", marginBottom: 4 }}>{t.firstPurchase}</div>
            <div style={{ fontSize: 22, fontWeight: 900, color: "#00f5ff", letterSpacing: 2 }}>WELCOME</div>
          </div>
          <div style={{
            background: "linear-gradient(135deg, #00f5ff, #0080ff)",
            color: "#000", fontWeight: 900, fontSize: 18,
            padding: "8px 14px", borderRadius: 10,
          }}>5%</div>
        </div>
        <button style={styles.buyBtn("#00f5ff")} onClick={() => setShowBonus(false)}>
          {t.close}
        </button>
      </div>
    </div>
  );

  const isGameOpen = !!selectedGame;

  return (
    <div style={styles.app}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Exo+2:wght@400;600;700;800;900&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        ::-webkit-scrollbar { display: none; }
        input::placeholder { color: #444; }
      `}</style>

      {/* Header */}
      <div style={styles.header}>
        {isGameOpen || tab === "topup" ? (
          <button onClick={goBack} style={{
            background: "#141a2e", border: "1px solid #ffffff11",
            color: "#aaa", padding: "8px 14px", borderRadius: 10,
            cursor: "pointer", fontSize: 13, fontWeight: 700,
          }}>← {lang === "tj" ? "Назад" : "Назад"}</button>
        ) : (
          <div style={styles.logo}>{t.appName}</div>
        )}
        <div style={{ display: "flex", gap: 4 }}>
          <button style={styles.langBtn(lang === "tj")} onClick={() => setLang("tj")}>TJ</button>
          <button style={styles.langBtn(lang === "ru")} onClick={() => setLang("ru")}>RU</button>
        </div>
      </div>

      {/* Content */}
      <div style={styles.content}>
        {isGameOpen ? (
          <GameScreen />
        ) : tab === "home" ? (
          <HomeScreen />
        ) : tab === "history" ? (
          <HistoryScreen />
        ) : tab === "topup" ? (
          <TopUpScreen />
        ) : tab === "profile" ? (
          <ProfileScreen />
        ) : null}
      </div>

      {/* Bottom Nav */}
      {!isGameOpen && (
        <div style={styles.bottomNav}>
          {[
            { key: "home", icon: "⊞", label: lang === "tj" ? "Асосӣ" : "Главная" },
            { key: "history", icon: "🕐", label: lang === "tj" ? "Таърих" : "История" },
            { key: "topup", icon: "💰", label: lang === "tj" ? "Пур кун" : "Баланс" },
            { key: "profile", icon: "👤", label: lang === "tj" ? "Профил" : "Профиль" },
          ].map(n => (
            <button key={n.key} style={styles.navItem(tab === n.key)} onClick={() => setTab(n.key)}>
              <span style={styles.navIcon}>{n.icon}</span>
              {n.label}
            </button>
          ))}
        </div>
      )}

      {showBonus && <BonusModal />}
    </div>
  );
}

