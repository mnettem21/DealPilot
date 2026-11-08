# Known fraud checks (the bank's existing list)

These are the OLD tricks. The bank already checks every deal for them, and they are marked in
`history/deals.csv` (`fraud_label`). A deal that trips one goes straight to Fraud Operations.

| Code | What it catches | How the bank checks |
|---|---|---|
| STMT_TAMPER | Edited bank statements | Each month's opening balance must equal the prior month's closing balance; listed transactions must add up to the summary totals. |
| REV_INFLATION | Revenue made up on paper | Stated annual revenue (application / tax return) more than 1.5x the annualized deposits in the bank statements. |
| ID_MISMATCH | Borrowed or fake identity | Name, date of birth or address on the photo ID disagrees with the application (beyond common nicknames). |
| DUP_EIN | Same company applying under a new name | EIN already used by a different legal name in history. |
| SHELL_ADDRESS | Business with no real premises | Business address is a mailbox store / PMB. |

**Important:** the new trick this weekend is NOT on this list. Deals built with it pass every check above.
