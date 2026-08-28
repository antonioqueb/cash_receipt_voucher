/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const PERIODS = [
    { key: "today", label: "Hoy" },
    { key: "week", label: "Semana" },
    { key: "month", label: "Mes" },
    { key: "year", label: "Año" },
    { key: "all", label: "Todo" },
    { key: "custom", label: "Fechas" },
];

function emptyForm(type) {
    return { entry_type: type || "out", amount: "", concept: "", category_id: "", paid_to: "", reference: "", notes: "" };
}

export class PettyCashDashboard extends Component {
    static template = "cash_receipt_voucher.PettyCashDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.periods = PERIODS;
        this.state = useState({
            loading: true,
            saving: false,
            period: "month",
            dateFrom: "",
            dateTo: "",
            data: null,
            form: emptyForm("out"),
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call("petty.cash.entry", "get_petty_dashboard", [], {
                period: this.state.period,
                date_from: this.state.dateFrom || false,
                date_to: this.state.dateTo || false,
            });
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

    async applyCustom() {
        if (!this.state.dateFrom || !this.state.dateTo) {
            this.notification.add("Selecciona fecha inicial y final.", { type: "warning" });
            return;
        }
        await this.load();
    }

    // ---- formato
    get kpis() {
        return (this.state.data && this.state.data.kpis) || {};
    }
    get sym() {
        return (this.state.data && this.state.data.currency_symbol) || "$";
    }
    money(v) {
        const n = parseFloat(v || 0);
        const s = Math.abs(n).toLocaleString("es-MX", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        return (n < 0 ? "-" : "") + this.sym + s;
    }

    // ---- registro rápido
    setFormType(type) {
        this.state.form = emptyForm(type);
    }

    get formIsOut() {
        return this.state.form.entry_type === "out";
    }

    get canSubmit() {
        const f = this.state.form;
        const amount = parseFloat(f.amount);
        if (!(amount > 0) || !(f.concept || "").trim()) return false;
        if (this.formIsOut && !f.category_id) return false;
        return true;
    }

    get exceedsBalance() {
        const amount = parseFloat(this.state.form.amount || 0);
        return this.formIsOut && amount > 0 && amount > (this.kpis.balance || 0) + 0.005;
    }

    async submit() {
        if (!this.canSubmit || this.state.saving) return;
        this.state.saving = true;
        try {
            const f = this.state.form;
            const res = await this.orm.call("petty.cash.entry", "quick_create", [{
                entry_type: f.entry_type,
                amount: parseFloat(f.amount),
                concept: f.concept,
                category_id: f.category_id ? parseInt(f.category_id) : false,
                paid_to: f.paid_to,
                reference: f.reference,
                notes: f.notes,
            }]);
            this.notification.add(
                (this.formIsOut ? "Egreso registrado: " : "Ingreso registrado: ") + res.name,
                { type: "success" }
            );
            this.state.form = emptyForm(f.entry_type);
            await this.load();
        } finally {
            this.state.saving = false;
        }
    }

    async receive(id) {
        await this.orm.call("petty.cash.entry", "action_receive", [[id]]);
        this.notification.add("Fondo recibido y registrado en Caja Chica.", { type: "success" });
        await this.load();
    }

    openEntry(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "petty.cash.entry",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openList() {
        this.action.doAction("cash_receipt_voucher.action_petty_cash_entry");
    }

    async print() {
        const act = await this.orm.call("petty.cash.entry", "action_print_period_report", [], {
            period: this.state.period,
            date_from: this.state.dateFrom || false,
            date_to: this.state.dateTo || false,
        });
        await this.action.doAction(act);
    }
}

registry.category("actions").add("petty_cash_dashboard", PettyCashDashboard);
