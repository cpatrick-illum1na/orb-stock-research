# Milestone 1 decisions update

User decisions: $100,000 starting equity; exclude verified halted securities; retain ETFs in the baseline and apply no additional IPO-age cutoff beyond 14 completed sessions; compare the paper's $0.0035/share/side commission assumption with Alpaca's actual applicable fee schedule; use Wilder ATR(14); use the final 15:55–16:00 ET bar close as the EOD exit proxy. Historical reference research should use SIP, and the free real-time screener should use IEX as a separate feed profile. Never mix feed observations in relative volume.

## Zero-cost universe decision

The user cannot pay for external data. AlgoSeek, CRSP direct licenses, Norgate, and other paid data vendors are removed from the implementation plan. The $0 historical plan is:

- Retrospective point-in-time S&P 500 subset from a public dated membership repository (2004 through its verified cutoff), subject to validation and archived commit hash.
- Alpaca free historical SIP bars for dates whose query `end` is at least 15 minutes old, subject to symbol-level validation (especially renamed/delisted securities).
- Alpaca Basic IEX for the real-time 9:35 screener; use IEX for both numerator and historical denominator in that live IEX profile.
- Archive Nasdaq Trader daily symbol files and SEC public EDGAR evidence prospectively to build a broad-market point-in-time universe from the first complete collection date forward.

This is not a full-market historical universe. Reports must label the retrospective study `S&P 500 point-in-time subset`, show Alpaca inactive/delisted intraday coverage, and disclose any missing bars. Do not call the result a full replication of the paper's approximately 7,000-stock universe. The optional HF Data Library is excluded from the project default because its terms say academic research only, not for trading.

One portfolio-control item remains unconfirmed: proposed 1.0% equity risk per position, 4.0% maximum aggregate open stop-risk, and 4.0× gross exposure (plus 0.5%/2.0% sensitivity). The starting balance is confirmed at $100,000.
