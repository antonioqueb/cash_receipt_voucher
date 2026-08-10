# -*- coding: utf-8 -*-
from odoo import api, models
import logging

_logger = logging.getLogger(__name__)


class AccountJournal(models.Model):
    _inherit = 'account.journal'

    @api.model
    def _som_ensure_caja_nacional(self):
        """Garantiza el diario de efectivo CAJA NACIONAL por compañía.

        Se invoca desde data XML en cada -u: si ya existe (por nombre) no
        hace nada; si no, lo crea como diario de efectivo — Odoo genera solo
        su cuenta contable de liquidez. Es la caja para el efectivo que NO
        debe mezclarse en el diario Efectivo (los recibos de efectivo siguen
        pagándose contra Efectivo vía _get_cash_journal).
        """
        for company in self.env['res.company'].sudo().search([]):
            existing = self.sudo().search([
                ('type', '=', 'cash'),
                ('company_id', '=', company.id),
                ('name', 'ilike', 'caja nacional'),
            ], limit=1)
            if existing:
                continue

            # Código corto único por compañía: CNAC, CNA1, CNA2...
            code = 'CNAC'
            if self.sudo().search_count([
                    ('code', '=', code), ('company_id', '=', company.id)]):
                for i in range(1, 10):
                    candidate = 'CNA%d' % i
                    if not self.sudo().search_count([
                            ('code', '=', candidate),
                            ('company_id', '=', company.id)]):
                        code = candidate
                        break

            journal = self.sudo().create({
                'name': 'CAJA NACIONAL',
                'type': 'cash',
                'code': code,
                'company_id': company.id,
            })
            _logger.info(
                '[SOM] Diario CAJA NACIONAL creado (id=%s, code=%s) para %s',
                journal.id, code, company.name,
            )
        return True
