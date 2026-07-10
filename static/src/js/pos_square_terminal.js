/** @odoo-module */

import { PaymentInterface } from "@point_of_sale/app/payment/payment_interface";
import { register_payment_method } from "@point_of_sale/app/store/pos_store";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";

/**
 * Square Terminal payment interface for Odoo 18 POS.
 *
 * Sends a terminal.checkout request to the configured Square device
 * and blocks the POS UI until the terminal returns success or failure.
 */
export class PaymentSquareTerminal extends PaymentInterface {
    async send_payment_request(uuid) {
        const order = this.pos.get_order();
        const line = order.get_selected_paymentline();
        if (!line) {
            return false;
        }

        const paymentMethod = this.payment_method_id;
        const amount = line.get_amount();
        const currency = this.pos.currency.name;
        const reference = order.uid || order.name;

        line.set_payment_status("waitingCard");

        try {
            const result = await this.env.services.orm.call(
                "pos.payment.method",
                "square_terminal_checkout",
                [[paymentMethod.id], amount, currency, reference, this.pos.config.id]
            );

            if (result.status === "success") {
                line.set_payment_status("done");
                line.transaction_id =
                    (result.payment_ids || []).join(",") || result.checkout_id;
                return true;
            }

            line.set_payment_status("retry");
            const msg =
                result.message ||
                (result.status === "cancel"
                    ? _t("Payment canceled on Square Terminal.")
                    : _t("Square Terminal payment failed."));
            this._showError(msg);
            return false;
        } catch (error) {
            line.set_payment_status("retry");
            const message =
                error?.data?.message ||
                error?.message ||
                _t("Square Terminal communication error.");
            this._showError(message);
            return false;
        }
    }

    async send_payment_cancel(order, uuid) {
        return true;
    }

    async send_payment_reversal() {
        return true;
    }

    _showError(message) {
        this.env.services.dialog.add(AlertDialog, {
            title: _t("Square Terminal"),
            body: message,
        });
    }
}

register_payment_method("square", PaymentSquareTerminal);
