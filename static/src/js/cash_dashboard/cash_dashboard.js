/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const PERIODS = [
    { key: "today", label: "Hoy" },
    { key: "week", label: "Semana" },
    { key: "month", label: "Mes" },
    { key: "quarter", label: "Trimestre" },
    { key: "year", label: "Año" },
    { key: "custom", label: "Personalizado" },
];

const CURRENCIES = [
    { key: "all_mxn", label: "Consolidado MXN" },
    { key: "mxn", label: "Solo MXN" },
    { key: "usd", label: "Solo USD" },
];

export class CashDashboard extends Component {
    static template = "cash_receipt_voucher.CashDashboard";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.periods = PERIODS;
        this.currencies = CURRENCIES;
        this.state = useState({
            loading: true,
            printing: false,
            // Por defecto: AÑO en curso y solo MXN (pedido del cliente, 1 sep 2026).
            period: "year",
            currencyMode: "mxn",
            dateFrom: "",
            dateTo: "",
            data: null,
        });
        onWillStart(() => this.load());
    }

    // ---------------------------------------------------------------- data
    async load() {
        this.state.loading = true;
        try {
            const data = await this.orm.call(
                "cash.entry",
                "get_dashboard_data",
                [],
                {
                    period: this.state.period,
                    date_from: this.state.dateFrom || false,
                    date_to: this.state.dateTo || false,
                    currency_mode: this.state.currencyMode,
                }
            );
            this.state.data = data;
        } catch (e) {
            this.notification.add("No se pudo cargar el dashboard de efectivo.", {
                type: "danger",
            });
            throw e;
        } finally {
            this.state.loading = false;
        }
    }

    async setPeriod(key) {
        this.state.period = key;
        if (key !== "custom") {
            await this.load();
        }
    }

    async setCurrency(key) {
        this.state.currencyMode = key;
        await this.load();
    }

    async applyCustom() {
        if (!this.state.dateFrom || !this.state.dateTo) {
            this.notification.add("Selecciona fecha inicial y final.", { type: "warning" });
            return;
        }
        this.state.period = "custom";
        await this.load();
    }

    async refresh() {
        await this.load();
    }

    async printPeriod() {
        this.state.printing = true;
        try {
            const act = await this.orm.call(
                "cash.entry",
                "action_print_period_report",
                [],
                {
                    period: this.state.period,
                    date_from: this.state.dateFrom || false,
                    date_to: this.state.dateTo || false,
                    currency_mode: this.state.currencyMode,
                }
            );
            await this.action.doAction(act);
        } catch (e) {
            this.notification.add("No hay entradas de caja para imprimir en este periodo.", {
                type: "warning",
            });
        } finally {
            this.state.printing = false;
        }
    }

    async openReceipt(id) {
        await this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "cash.entry",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    // ---------------------------------------------------------------- format
    get cur() {
        return (this.state.data && this.state.data.currency) || { symbol: "$", position: "before", label: "MXN" };
    }
    get curLabel() {
        return this.cur.label || "MXN";
    }
    get mix() {
        return (this.state.data && this.state.data.mix) || { usd: 0, mxn: 0 };
    }

    money(val) {
        const n = parseFloat(val || 0);
        const s = Math.abs(n).toLocaleString("es-MX", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
        const sign = n < 0 ? "-" : "";
        return this.cur.position === "after"
            ? `${sign}${s} ${this.cur.symbol}`
            : `${sign}${this.cur.symbol}${s}`;
    }

    int(val) {
        return Math.round(parseFloat(val || 0)).toLocaleString("es-MX");
    }

    pct(val) {
        return `${parseFloat(val || 0).toFixed(1)}%`;
    }

    // ---------------------------------------------------------------- charts
    get kpis() {
        return (this.state.data && this.state.data.kpis) || {};
    }
    get series() {
        return (this.state.data && this.state.data.series) || [];
    }
    get seriesLabels() {
        return (this.state.data && this.state.data.series_labels) || [];
    }
    get topPartners() {
        return (this.state.data && this.state.data.top_partners) || [];
    }
    get recent() {
        return (this.state.data && this.state.data.recent) || [];
    }
    get recentOut() {
        return (this.state.data && this.state.data.recent_out) || [];
    }

    openDisbursement(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "cash.disbursement",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    get seriesMax() {
        let m = 0;
        for (const b of this.series) {
            m = Math.max(m, b.official || 0, b.out || 0);
        }
        return m || 1;
    }

    barH(val) {
        return Math.max(0, Math.min(100, (parseFloat(val || 0) / this.seriesMax) * 100));
    }

    get partnerMax() {
        let m = 0;
        for (const p of this.topPartners) {
            m = Math.max(m, p.real || 0);
        }
        return m || 1;
    }

    partnerW(val) {
        return Math.max(2, Math.min(100, (parseFloat(val || 0) / this.partnerMax) * 100));
    }

    // Donut: proporción de efectivo real vs faltante sobre el oficial.
    get donut() {
        const k = this.kpis;
        const official = k.total_official || 0;
        const real = k.total_real || 0;
        const C = 2 * Math.PI * 54; // circunferencia (r=54)
        const realPct = official > 0 ? Math.max(0, Math.min(1, real / official)) : 0;
        return {
            circ: C,
            realDash: `${realPct * C} ${C}`,
            realPct: realPct * 100,
        };
    }

    diffClass(val) {
        const n = parseFloat(val || 0);
        if (n > 0) return "o_cash_neg"; // faltante
        if (n < 0) return "o_cash_pos"; // sobrante
        return "o_cash_zero";
    }

    diffLabel(val) {
        const n = parseFloat(val || 0);
        if (n > 0) return "Retenido";
        if (n < 0) return "Depositado de más";
        return "Sin retención";
    }

    // ---------------------------------------------------------------- extras
    get max() {
        return (this.state.data && this.state.data.max_receipt) || {};
    }
    get prev() {
        return (this.state.data && this.state.data.prev) || {};
    }
    get deltas() {
        return (this.state.data && this.state.data.deltas) || {};
    }
    get states() {
        return (this.state.data && this.state.data.states) || {};
    }
    get retentionPartners() {
        return (this.state.data && this.state.data.retention_partners) || [];
    }

    get retMax() {
        let m = 0;
        for (const p of this.retentionPartners) {
            m = Math.max(m, p.diff || 0);
        }
        return m || 1;
    }
    retW(v) {
        return Math.max(2, Math.min(100, (parseFloat(v || 0) / this.retMax) * 100));
    }

    // Gauge / termómetro semicircular (0..100%) — r debe coincidir con el arco SVG
    gauge(value) {
        const r = 56;
        const semi = Math.PI * r;
        const pct = Math.max(0, Math.min(100, parseFloat(value || 0)));
        return { semi, dash: `${(pct / 100) * semi} ${semi}`, pct };
    }

    deltaClass(v) {
        const n = parseFloat(v || 0);
        if (n > 0) return "o_cash_up";
        if (n < 0) return "o_cash_down";
        return "o_cash_flat";
    }
    deltaText(v) {
        const n = parseFloat(v || 0);
        const a = Math.abs(n).toFixed(1);
        return (n > 0 ? "▲ " : n < 0 ? "▼ " : "= ") + a + "%";
    }

    periodLabel() {
        const found = PERIODS.find((p) => p.key === this.state.period);
        return found ? found.label : "";
    }

    // ---------------------------------------------------------------- rediseño
    // Formato corto para ejes/etiquetas: 1.2 k · 3.4 M
    short(val) {
        const n = parseFloat(val || 0);
        const a = Math.abs(n);
        const sign = n < 0 ? "-" : "";
        if (a >= 1e6) return sign + (a / 1e6).toFixed(a >= 1e7 ? 0 : 1) + " M";
        if (a >= 1e3) return sign + (a / 1e3).toFixed(a >= 1e5 ? 0 : 1) + " k";
        return sign + a.toFixed(0);
    }

    // Líneas guía del gráfico de flujo (100/75/50/25/0 % del máximo)
    get gridTicks() {
        const max = this.seriesMax;
        return [100, 75, 50, 25].map((p) => ({ pct: p, label: this.short((max * p) / 100) }));
    }

    // Sparkline de entradas (SVG 100×32) para la tarjeta de Entradas
    get sparkPoints() {
        const s = this.series;
        if (s.length < 2) return "";
        const max = Math.max(...s.map((b) => b.official || 0), 1);
        const w = 100, h = 32;
        return s
            .map((b, i) => `${((i / (s.length - 1)) * w).toFixed(2)},${(h - ((b.official || 0) / max) * (h - 3) - 1.5).toFixed(2)}`)
            .join(" ");
    }
    get sparkArea() {
        const pts = this.sparkPoints;
        return pts ? `0,32 ${pts} 100,32` : "";
    }

    // Donut de balance: entradas vs salidas del periodo
    get flowDonut() {
        const k = this.kpis;
        const inn = Math.max(parseFloat(k.total_official || 0), 0);
        const out = Math.max(parseFloat(k.total_out || 0), 0);
        const tot = inn + out;
        const C = 2 * Math.PI * 54;
        const pIn = tot > 0 ? inn / tot : 0;
        return {
            circ: C,
            inDash: `${pIn * C} ${C}`,
            outDash: `${(1 - pIn) * C} ${C}`,
            outOffset: -(pIn * C),
            pIn: Math.round(pIn * 100),
            pOut: Math.round((1 - pIn) * 100),
            empty: tot <= 0,
        };
    }

    // Participación de cada cliente en el ranking
    get partnersTotal() {
        return this.topPartners.reduce((a, p) => a + (parseFloat(p.real) || 0), 0) || 1;
    }
    share(p) {
        return ((parseFloat(p.real) || 0) / this.partnersTotal) * 100;
    }
    sharePct(p) {
        return this.share(p).toFixed(1) + "%";
    }
    // Origen de entradas (recibo vs manual) como barra apilada
    get originPct() {
        const s = this.states;
        const linked = parseInt(s.linked || 0), manual = parseInt(s.manual || 0);
        const tot = linked + manual;
        return tot ? Math.round((linked / tot) * 100) : 0;
    }
    get periodSummary() {
        const k = this.kpis;
        return {
            inn: k.total_official || 0,
            out: k.total_out || 0,
            net: (k.total_official || 0) - (k.total_out || 0),
        };
    }
}

registry.category("actions").add("cash_dashboard", CashDashboard);
