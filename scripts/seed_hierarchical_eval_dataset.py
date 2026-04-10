import os
import json
from langfuse import Langfuse
from models.eval_schemas import HierarchicalDatasetItem, GoldenEpic, GoldenTicket

# Initialize Langfuse client
os.environ["LANGFUSE_PUBLIC_KEY"] = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
os.environ["LANGFUSE_SECRET_KEY"] = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
os.environ["LANGFUSE_HOST"] = os.getenv("LANGFUSE_HOST", "http://localhost:3002")

def seed_hierarchical_dataset():
    langfuse = Langfuse()
    dataset_name = "sow-hierarchical-golden-set"
    
    print(f"Creating/getting dataset: {dataset_name}...")
    langfuse.create_dataset(
        name=dataset_name,
        description="Hierarchical Golden Dataset (EPIC -> Tickets) for SOW extraction evaluation.",
        metadata={"domain": "trade-finance", "hierarchy": "epic-story"}
    )

    # Define the Epics and their associated features from the original seed script
    epics_data = [
        {
            "id": "IAM",
            "title": "Identity & Access Management",
            "description": "User registration, KYC, RBAC, MFA, and DID management.",
            "features": ["IAM-01", "IAM-02", "IAM-03", "IAM-04", "IAM-05", "IAM-06"]
        },
        {
            "id": "AP",
            "title": "Asset Provider Operations",
            "description": "Gold and Cash deposits, withdrawals, and provider dashboard.",
            "features": ["AP-01", "AP-02", "AP-03", "AP-04", "AP-05"]
        },
        {
            "id": "TM",
            "title": "Trade Management",
            "description": "Trade applications, credit scoring, milestone payments, NFTs, escrow, and off-ramps.",
            "features": ["TM-01", "TM-02", "TM-03", "TM-04", "TM-05", "TM-06", "TM-07"]
        },
        {
            "id": "CR",
            "title": "Compliance & Regulatory",
            "description": "AML, transaction monitoring, proof of reserves, risk dashboard, and audit trails.",
            "features": ["CR-01", "CR-02", "CR-03", "CR-04", "CR-05", "CR-06"]
        },
        {
            "id": "YE",
            "title": "Yield Engine",
            "description": "Yield calculation, distribution triggers, and LP value updates.",
            "features": ["YE-01", "YE-02", "YE-03"]
        }
    ]

    # Map of all features (copied and adapted from seed_full_langfuse_dataset.py)
    all_features = {
        "IAM-01": {"name": "User Registration", "desc": "Self-service registration for Providers and Traders. Entity type selection (Gold Provider, Cash Provider, Trader), email verification, profile completion with company details.", "ac": ["User registers, verifies email, selects entity type, completes profile", "Duplicate email prevented", "Registration event logged", "Admin notified of new registration"]},
        "IAM-02": {"name": "KYC Integration", "desc": "Shufti Pro integration for identity verification, document verification (passport/Emirates ID), sanctions screening, PEP check. UAE entity verification: trade license validation, UBO declaration.", "ac": ["KYC flow completes end-to-end", "Verified status written to DIDVerifier on-chain", "Rejection reason displayed to user", "Admin can view KYC status and override", "Re-verification workflow works"]},
        "IAM-03": {"name": "Role-Based Access", "desc": "Five roles: Gold Provider, Cash Provider, Trader, Admin, Compliance Officer. Per-portal access control with documented permission matrix.", "ac": ["Each role can only access permitted portals/features", "Unauthorized access returns 403", "Role assignment requires admin approval", "Permission matrix documented and enforced"]},
        "IAM-04": {"name": "MFA Authentication", "desc": "TOTP-based multi-factor authentication via authenticator apps. Mandatory for admin roles and all high-value operations exceeding $50K.", "ac": ["MFA enrollment works with Google Authenticator / Authy", "High-value operations blocked without MFA", "Recovery flow functional", "Admin sessions always require MFA"]},
        "IAM-05": {"name": "On-Chain DID", "desc": "DIDVerifier.sol stores boolean isVerified flag per address. Maps platform user ID to blockchain wallet address. Only verified addresses can transact on-chain.", "ac": ["Verified users have on-chain flag true", "Non-verified addresses rejected by all smart contracts", "Mapping queryable", "Revocation works for compliance actions"]},
        "IAM-06": {"name": "Wallet Provisioning", "desc": "Auto-generate HD wallet (BIP-44 path) on registration. Custodial model with HSM-managed keys (AWS CloudHSM). Unique address per user. No private key exposure.", "ac": ["Every registered user gets unique blockchain address", "Keys stored in HSM", "Derivation follows BIP-44", "No private key visible in any log, API response, or database field"]},
        
        "AP-01": {"name": "Gold Deposit", "desc": "Multi-step workflow: gold details entry (weight, purity, custodian, vault), 4-document upload (Certificate of Ownership, Assay Report, Insurance, Custodian Confirmation), custodian verification (email-based MVP), compliance check, admin approval, on-chain registration, TCT minting.", "ac": ["Full flow from entry to TCT minting", "All 4 documents uploadable (PDF, max 10MB each)", "Custodian verification email sent with template", "Admin approval triggers on-chain registration", "Rejection requires reason"]},
        "AP-02": {"name": "Cash Deposit", "desc": "Multi-step: amount entry (min $10K), source of funds document upload, wire instructions display (bank/account/reference), banking API receipt confirmation, AML screening, CCT token minting, LP token issuance.", "ac": ["Full flow from entry to CCT minting", "Wire instructions displayed correctly", "Banking API confirms receipt", "AML screening runs before minting", "LP tokens reflect correct pool share"]},
        "AP-03": {"name": "Gold Withdrawal", "desc": "Full or partial withdrawal request. Available liquidity check. FIFO queue if pool liquidity insufficient. Accumulated yield calculation. LP token + TCT burn. Settlement: fiat wire (T+1) or physical gold release (T+5 business days).", "ac": ["Withdrawal request created", "Liquidity check accurate", "FIFO queue activates correctly when pool insufficient", "Token burn reflected on-chain", "Wire/gold release initiated within SLA"]},
        "AP-04": {"name": "Cash Withdrawal", "desc": "Full or partial. Liquidity check. FIFO queue. Yield calculation to withdrawal date. LP + CCT burn. Fiat wire to registered bank account (T+1).", "ac": ["Same as AP-03 for cash", "Wire initiated within T+1", "Correct yield calculated", "LP share reduced accurately", "FIFO queue position displayed to user"]},
        "AP-05": {"name": "Provider Dashboard", "desc": "Real-time portfolio view: total deposited (gold oz / cash USD), current valuation (gold at LBMA price), LP share %, accumulated yield, projected APY, active trade exposure, withdrawal queue position (if any).", "ac": ["Dashboard loads within 3 seconds", "All values accurate within 1-minute lag", "Gold price updates every 15 minutes", "Yield updates daily", "Export to PDF functional"]},

        "TM-01": {"name": "Trade Application", "desc": "Upload trade documents (SBLC/ICPO/SCO/LOI, invoices, shipping docs), enter deal details (commodity, quantity, value, counterparty, proposed milestones). AI document verification. Application status tracking.", "ac": ["Application submits successfully", "All document types uploadable", "AI verification returns confidence score", "Status visible in Trader Portal", "Admin notified of new application"]},
        "TM-02": {"name": "Credit Scoring", "desc": "Risk assessment engine. MVP: Rules-based scoring with manual admin override (cold-start approach for 3 pilot deals with zero historical data). Factors: trade value, commodity type, counterparty profile, documentation quality. Future: XGBoost ML model.", "ac": ["Credit score generated for every application", "Rules produce consistent results", "Admin can override with documented reason", "Score affects credit limit assignment", "History logged for future ML training"]},
        "TM-03": {"name": "Milestone Payments", "desc": "Smart contract-managed 30/40/30 split. MilestonePayment.sol state machine: PENDING > APPROVED > RELEASED > COMPLETED (or DISPUTED). Escrow lock/release per milestone. Documentation verification at each gate.", "ac": ["3 milestones created per trade", "State transitions work correctly", "Escrow locks/releases accurate amounts", "Dispute transition functional", "Cannot release without admin approval"]},
        "TM-04": {"name": "Trade NFT", "desc": "ERC-721 NFT minted per trade deal, encoding: deal value, parties, commodity, milestones, terms, status. Immutable trade record on-chain. Status updates as milestones progress.", "ac": ["NFT minted on deal creation", "Metadata includes all deal terms", "Status updates on milestone events", "NFT queryable by all parties", "Transfer restricted (soulbound to deal)"]},
        "TM-05": {"name": "Escrow Management", "desc": "EscrowVault.sol locks financing amount from pool. Per-milestone release authorization. Refund capability for disputes. Emergency admin release with multisig.", "ac": ["Funds lock correctly from pool", "Release per milestone accurate to the wei", "Refund works for dispute outcomes", "Emergency release requires 3-of-5 multisig"]},
        "TM-06": {"name": "Repayment Tracking", "desc": "Track buyer repayment against financing terms. Automated reminders. Late payment detection and penalty calculation (2% per 30-day period). Credit score impact for late/missed payments.", "ac": ["Repayment schedule created on deal approval", "Reminders sent at 7 days and 1 day before due", "Late payment detected and penalty calculated accurately", "Credit score updated on late event"]},
        "TM-07": {"name": "Chill Pay Off-Ramp", "desc": "Supplier token-to-fiat conversion. Integration model: platform redirects supplier to Chill Pay hosted checkout (not embedded). Supported currencies: AED, USD. Settlement timeline: T+1 for AED, T+2 for USD. Chill Pay handles their own KYC.", "ac": ["Supplier can initiate conversion from portal", "Redirect to Chill Pay works", "Conversion amount and currency correct", "Settlement completes within SLA", "Transaction status reflected back on platform"]},

        "CR-01": {"name": "AML Screening", "desc": "Automated sanctions screening on every transaction. GOAML integration for UAE compliance reporting. Suspicious Transaction Report (STR) generation. Watchlist matching (UN, OFAC, EU).", "ac": ["Every deposit/withdrawal/trade triggers screening", "Flagged transactions blocked pending review", "GOAML-format reports exportable", "STR generated with required fields", "Watchlist updated weekly"]},
        "CR-02": {"name": "Transaction Monitoring", "desc": "Real-time monitoring for unusual patterns: velocity, amount thresholds, geographic risk, behavioral anomalies. Alert generation for compliance officer review.", "ac": ["Alerts generated for transactions exceeding thresholds", "Compliance officer queue populated", "False positive rate tracked", "Alert resolution workflow complete", "Audit trail for every alert"]},
        "CR-03": {"name": "Proof of Reserves", "desc": "Real-time dashboard comparing: on-chain token supply (TCT + CCT) versus off-chain verified reserves (gold + cash). Backing ratio displayed. Alert if ratio approaches 100%.", "ac": ["Dashboard accurate within 15-minute lag", "Backing ratio calculation correct", "Alert fires at 102% threshold", "Historical backing ratio trend visible", "Exportable for regulator reporting"]},
        "CR-04": {"name": "Risk Dashboard", "desc": "Unified risk view: pool utilization %, concentration per trade, default probability scores, provider exposure breakdown, active dispute count, regulatory compliance status.", "ac": ["Dashboard loads with all risk metrics", "Pool utilization real-time", "Concentration limit violations flagged", "All data points sourced from live platform data"]},
        "CR-05": {"name": "Regulator Observer", "desc": "Read-only portal for VARA compliance auditors. Transaction browser, audit trail viewer, compliance report generator, real-time pool status. No write/modify capability.", "ac": ["Observer can log in with restricted role", "All transactions viewable", "Audit trail searchable by date/type/party", "No edit/delete capabilities available", "Session logging active"]},
        "CR-06": {"name": "Audit Trail", "desc": "Immutable log of every platform action: user actions, admin decisions, smart contract events, system events. Tamper-evident (hash-chained). Searchable and exportable.", "ac": ["Every action logged with timestamp, user, action type, before/after state", "Logs hash-chained for tamper detection", "Search by date range, user, action type", "Export to CSV/PDF"]},

        "YE-01": {"name": "Yield Calculation", "desc": "Daily calculation of provider yield from trade financing fees. Formula: Provider Yield = (LP Share % x 70% of Total Fees Collected) / Time Period. Compounding frequency: daily accrual, monthly distribution.", "ac": ["Yield calculated daily at UTC midnight", "Accurate to 6 decimal places", "Matches manual calculation within 0.01% tolerance", "Handles partial-period deposits correctly"]},
        "YE-02": {"name": "Distribution Trigger", "desc": "Monthly automated yield distribution to all active LP holders. Pro-rata based on time-weighted average LP share. Distribution event logged on-chain. Provider notification sent.", "ac": ["Distribution executes monthly", "All eligible providers receive correct amounts", "On-chain distribution event emitted", "Email/portal notification sent", "Distribution history viewable"]},
        "YE-03": {"name": "LP Value Update", "desc": "Real-time LP token value calculation. Factors: underlying asset value (gold at LBMA + cash at par), accumulated yield, pool utilization. LP price displayed on Provider Dashboard.", "ac": ["LP value updates every 15 minutes", "Reflects gold price changes, new deposits, withdrawals, and yield accrual", "Historical LP value chart available"]}
    }

    for epic_meta in epics_data:
        tickets = []
        full_text_parts = [epic_meta["description"]]
        
        for feat_id in epic_meta["features"]:
            feat = all_features[feat_id]
            tickets.append(GoldenTicket(
                title=f"Implement {feat['name']}",
                short_description=feat["desc"],
                acceptance_criteria=feat["ac"]
            ))
            full_text_parts.append(f"Feature: {feat['name']}\nDescription: {feat['desc']}\nAC: {', '.join(feat['ac'])}")
            
        epic = GoldenEpic(
            title=epic_meta["title"],
            short_description=epic_meta["description"],
            tickets=tickets
        )
        
        item = HierarchicalDatasetItem(epic=epic)
        
        print(f"Adding Epic: {epic.title}...")
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={
                "section_title": epic.title,
                "section_text": "\n\n".join(full_text_parts)
            },
            expected_output=json.loads(item.json())
        )

    print(f"Dataset '{dataset_name}' successfully seeded!")

if __name__ == "__main__":
    seed_hierarchical_dataset()
