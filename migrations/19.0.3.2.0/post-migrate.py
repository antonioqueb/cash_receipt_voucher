# -*- coding: utf-8 -*-
def migrate(cr, version):
    """Corrige el efectivo en caja (diferencia) almacenado.

    La migración 3.1.0 puso 'amount_internal = amount' por SQL en los recibos
    históricos, pero el campo computado-almacenado 'amount_internal_diff' NO se
    recalcula con un UPDATE SQL, así que quedó con su valor viejo (= al total).
    Aquí se vuelve a alinear y se recalcula la diferencia y el flag, ya con la
    semántica correcta: en caja = cobrado − depositado a cuenta."""
    # 1) Históricos sin ajuste manual: lo depositado = lo cobrado.
    cr.execute("""
        UPDATE cash_receipt
           SET amount_internal = amount
         WHERE (amount_internal IS NULL OR amount_internal = 0)
           AND internal_adjusted_by IS NULL
    """)
    # 2) Recalcular el campo almacenado (el UPDATE de arriba no dispara compute).
    cr.execute("""
        UPDATE cash_receipt
           SET amount_internal_diff = COALESCE(amount, 0) - COALESCE(amount_internal, 0),
               has_internal_diff = (COALESCE(amount, 0) - COALESCE(amount_internal, 0)) <> 0
    """)
