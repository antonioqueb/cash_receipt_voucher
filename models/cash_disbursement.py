# -*- coding: utf-8 -*-
"""Salidas de caja chica (control interno de efectivo).

Cierra el ciclo del control interno: el recibo de efectivo registra la
ENTRADA; este modelo registra la SALIDA (gastos/entregas de caja chica).
El saldo de caja del dashboard disminuye automáticamente con cada salida.

Es una capa interna paralela: NO toca contabilidad ni pagos oficiales.
Solo el grupo "Ajuste de Efectivo Real" puede capturar/editar; el grupo
de Consulta solo ve.
"""
from odoo import models, fields, api, _
from odoo.exceptions import ValidationError


class CashDisbursement(models.Model):
    _name = 'cash.disbursement'
    _description = 'Salida de Caja Chica'
    _inherit = ['mail.thread']
    _order = 'date desc, id desc'
    _rec_name = 'name'

    # Folio único por compañía (mismo criterio que los recibos).
    _name_company_uniq = models.Constraint(
        'unique(name, company_id)',
        'Ya existe una salida de caja con ese folio en esta compañía.',
    )

    name = fields.Char(
        string='Folio', required=True, copy=False, readonly=True,
        default=lambda self: _('Nuevo'),
    )
    date = fields.Datetime(
        string='Fecha', required=True, default=fields.Datetime.now,
        tracking=True,
    )
    amount = fields.Monetary(
        string='Monto', required=True, tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency', string='Divisa', required=True,
        default=lambda self: self.env.ref(
            'base.MXN', raise_if_not_found=False) or self.env.company.currency_id,
    )
    delivered_to = fields.Char(
        string='Entregado a', required=True, tracking=True,
        help='Persona que recibió el efectivo.',
    )
    partner_id = fields.Many2one(
        'res.partner', string='Contacto (opcional)',
        help='Contacto relacionado, si aplica.',
    )
    purchase_order_id = fields.Many2one(
        'purchase.order', string='Orden de Compra',
        tracking=True,
        help='Orden de compra que motiva la salida (búscala por folio). Opcional.',
    )
    concept = fields.Text(
        string='Concepto / Motivo interno', required=True,
    )
    user_id = fields.Many2one(
        'res.users', string='Usuario responsable', required=True,
        default=lambda self: self.env.user, readonly=True,
        help='Quién registró la salida de caja.',
    )
    company_id = fields.Many2one(
        'res.company', string='Compañía', required=True,
        default=lambda self: self.env.company,
    )
    state = fields.Selection([
        ('done', 'Registrada'),
        ('cancelled', 'Cancelada'),
    ], string='Estado', default='done', tracking=True)

    # Equivalente MXN con el tipo de cambio de la fecha (mismo criterio que
    # los recibos: tasas DOF/Banxico en res.currency.rate).
    mxn_currency_id = fields.Many2one(
        'res.currency', compute='_compute_ref_currencies',
    )
    amount_mxn = fields.Monetary(
        string='Monto (MXN)', compute='_compute_amount_mxn', store=True,
        currency_field='mxn_currency_id',
    )

    def _compute_ref_currencies(self):
        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        for rec in self:
            rec.mxn_currency_id = mxn

    @api.depends('amount', 'currency_id', 'date')
    def _compute_amount_mxn(self):
        mxn = self.env.ref('base.MXN', raise_if_not_found=False)
        for rec in self:
            company = rec.company_id or self.env.company
            target = mxn or company.currency_id
            d = rec.date.date() if rec.date else fields.Date.context_today(rec)
            if rec.currency_id and target and rec.currency_id != target:
                rec.amount_mxn = rec.currency_id._convert(
                    rec.amount or 0.0, target, company, d)
            else:
                rec.amount_mxn = rec.amount or 0.0

    @api.onchange('purchase_order_id')
    def _onchange_purchase_order_company(self):
        for rec in self:
            po_company = rec.purchase_order_id.company_id
            if po_company and rec.company_id != po_company:
                rec.company_id = po_company

    @api.constrains('amount')
    def _check_amount_positive(self):
        for rec in self:
            if rec.amount <= 0:
                raise ValidationError(_(
                    'El monto de la salida de caja debe ser mayor a cero.'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # Compañía = la de la orden de compra ligada (no la activa).
            if not vals.get('company_id') and vals.get('purchase_order_id'):
                po = self.env['purchase.order'].browse(vals['purchase_order_id']).sudo()
                if po.company_id:
                    vals['company_id'] = po.company_id.id
            company = (self.env['res.company'].browse(vals['company_id'])
                       if vals.get('company_id') else self.env.company)
            if vals.get('name', _('Nuevo')) == _('Nuevo'):
                vals['name'] = self.env['cash.receipt']._som_next_sequence(
                    'cash.disbursement', company) or _('Nuevo')
        return super().create(vals_list)

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True

    def action_restore(self):
        self.write({'state': 'done'})
        return True
