# -*- coding: utf-8 -*-
def migrate(cr, version):
    """Recibos históricos sin ajuste interno: el 'Depositado a Cuenta' debe ser
    igual al 'Monto Recibido' (no había retención registrada). Antes quedaban en
    0 porque el campo no existía cuando se crearon, y eso hacía que el efectivo
    en caja (diferencia) saliera igual al total. Solo se tocan los que NO fueron
    ajustados manualmente (internal_adjusted_by IS NULL)."""
    cr.execute("""
        UPDATE cash_receipt
           SET amount_internal = amount
         WHERE (amount_internal IS NULL OR amount_internal = 0)
           AND internal_adjusted_by IS NULL
    """)
