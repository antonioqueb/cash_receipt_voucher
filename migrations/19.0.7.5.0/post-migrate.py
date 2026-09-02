# -*- coding: utf-8 -*-
"""19.0.7.5.0 — Facturas alineadas con la orden.

* Órdenes confirmadas con MENOS facturado que la orden: se genera la
  factura complementaria (publicada). El dinero del cliente que estaba sin
  aplicar la cubre en cuanto corra la aplicación automática de cobros.
* Órdenes con MÁS facturado que la orden (facturas duplicadas o líneas
  quitadas): NO se genera nota de crédito a ciegas. Se apaga la alineación
  automática de esa orden, se deja nota en el chatter y quedan para que
  contabilidad las revise y use el botón "Alinear facturas con la orden".
* Órdenes con líneas facturadas que no se pueden atribuir: solo aviso.
"""
import logging

from odoo import api, SUPERUSER_ID

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    SO = env['sale.order'].sudo()
    orders = SO.search([('state', '=', 'sale'), ('invoice_ids.state', '=', 'posted')])
    under, over, problems = SO, SO, []
    for so in orders:
        if not so._som_posted_customer_invoices():
            continue
        deltas, note = so._som_invoice_deltas()
        if note:
            problems.append('%s: %s' % (so.name, note))
            continue
        gap = sum(d for _l, d in deltas)
        if gap > 0.005:
            under |= so
        elif gap < -0.005:
            over |= so
    created = under._som_sync_invoices(force=True)
    for so in over:
        so.write({'x_invoice_sync': False})
        so.message_post(
            body='⚠️ Esta orden tiene MÁS facturado que su total (%s %s de más). La alineación '
                 'automática quedó apagada para no generar una nota de crédito a ciegas: revisa las '
                 'facturas (¿duplicada?, ¿líneas quitadas?) y usa "Alinear facturas con la orden".'
                 % (so.currency_id.symbol or '', '{:,.2f}'.format(-so.x_invoice_gap)),
            message_type='notification')
    _logger.info('[cash_receipt_voucher 7.5.0] %d orden(es) con faltante facturado -> %d documento(s); '
                 '%d con exceso facturado (alineación apagada, revisar): %s; %d con líneas no atribuibles: %s',
                 len(under), len(created), len(over), ', '.join(over.mapped('name')),
                 len(problems), ' | '.join(problems))
