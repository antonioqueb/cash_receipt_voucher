"""Folios de efectivo sin huecos (no_gap) en bases existentes.

Las secuencias se cargan con noupdate="1", así que editar el XML no toca los
registros ya creados: este script las actualiza. Un control interno de
efectivo con huecos de folio (por cada rollback) pierde valor probatorio.
"""
import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    if not version:
        return

    cr.execute(
        """
        UPDATE ir_sequence
        SET implementation = 'no_gap'
        WHERE code IN ('cash.receipt', 'cash.disbursement')
          AND implementation IS DISTINCT FROM 'no_gap'
        """
    )
    _logger.info(
        '[cash_receipt_voucher] Secuencias de efectivo migradas a no_gap: %s',
        cr.rowcount,
    )
