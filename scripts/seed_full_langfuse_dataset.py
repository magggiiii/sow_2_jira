import os
from langfuse import Langfuse

# Initialize Langfuse client
os.environ["LANGFUSE_PUBLIC_KEY"] = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
os.environ["LANGFUSE_SECRET_KEY"] = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
os.environ["LANGFUSE_HOST"] = os.getenv("LANGFUSE_HOST", "http://localhost:3002")

def seed_dataset():
    langfuse = Langfuse()

    dataset_name = "sow-extraction-full-ground-truth"
    
    print(f"Creating/getting dataset: {dataset_name}...")
    langfuse.create_dataset(
        name=dataset_name,
        description="Comprehensive ground truth Jira tickets extracted from all 27 features in the Raw Essentials SOW MVP Phase 0.",
        metadata={"domain": "trade-finance", "source": "test_sow.pdf"}
    )

    items = [
        # IAM
        {
            "feature_id": "IAM-01",
            "feature_name": "User Registration",
            "description": "Self-service registration for Providers and Traders. Entity type selection (Gold Provider, Cash Provider, Trader), email verification, profile completion with company details.",
            "acceptance_criteria": "User registers, verifies email, selects entity type, completes profile. Duplicate email prevented. Registration event logged. Admin notified of new registration."
        },
        {
            "feature_id": "IAM-02",
            "feature_name": "KYC Integration",
            "description": "Shufti Pro integration for identity verification, document verification (passport/Emirates ID), sanctions screening, PEP check. UAE entity verification: trade license validation, UBO declaration.",
            "acceptance_criteria": "KYC flow completes end-to-end. Verified status written to DIDVerifier on-chain. Rejection reason displayed to user. Admin can view KYC status and override. Re-verification workflow works."
        },
        {
            "feature_id": "IAM-03",
            "feature_name": "Role-Based Access",
            "description": "Five roles: Gold Provider, Cash Provider, Trader, Admin, Compliance Officer. Per-portal access control with documented permission matrix.",
            "acceptance_criteria": "Each role can only access permitted portals/features. Unauthorized access returns 403. Role assignment requires admin approval. Permission matrix documented and enforced."
        },
        {
            "feature_id": "IAM-04",
            "feature_name": "MFA Authentication",
            "description": "TOTP-based multi-factor authentication via authenticator apps. Mandatory for admin roles and all high-value operations exceeding $50K.",
            "acceptance_criteria": "MFA enrollment works with Google Authenticator / Authy. High-value operations blocked without MFA. Recovery flow functional. Admin sessions always require MFA."
        },
        {
            "feature_id": "IAM-05",
            "feature_name": "On-Chain DID",
            "description": "DIDVerifier.sol stores boolean isVerified flag per address. Maps platform user ID to blockchain wallet address. Only verified addresses can transact on-chain.",
            "acceptance_criteria": "Verified users have on-chain flag true. Non-verified addresses rejected by all smart contracts. Mapping queryable. Revocation works for compliance actions."
        },
        {
            "feature_id": "IAM-06",
            "feature_name": "Wallet Provisioning",
            "description": "Auto-generate HD wallet (BIP-44 path) on registration. Custodial model with HSM-managed keys (AWS CloudHSM). Unique address per user. No private key exposure.",
            "acceptance_criteria": "Every registered user gets unique blockchain address. Keys stored in HSM. Derivation follows BIP-44. No private key visible in any log, API response, or database field."
        },
        
        # Asset Provider Operations (AP)
        {
            "feature_id": "AP-01",
            "feature_name": "Gold Deposit",
            "description": "Multi-step workflow: gold details entry (weight, purity, custodian, vault), 4-document upload (Certificate of Ownership, Assay Report, Insurance, Custodian Confirmation), custodian verification (email-based MVP), compliance check, admin approval, on-chain registration, TCT minting.",
            "acceptance_criteria": "Full flow from entry to TCT minting. All 4 documents uploadable (PDF, max 10MB each). Custodian verification email sent with template. Admin approval triggers on-chain registration. Rejection requires reason."
        },
        {
            "feature_id": "AP-02",
            "feature_name": "Cash Deposit",
            "description": "Multi-step: amount entry (min $10K), source of funds document upload, wire instructions display (bank/account/reference), banking API receipt confirmation, AML screening, CCT token minting, LP token issuance.",
            "acceptance_criteria": "Full flow from entry to CCT minting. Wire instructions displayed correctly. Banking API confirms receipt. AML screening runs before minting. LP tokens reflect correct pool share."
        },
        {
            "feature_id": "AP-03",
            "feature_name": "Gold Withdrawal",
            "description": "Full or partial withdrawal request. Available liquidity check. FIFO queue if pool liquidity insufficient. Accumulated yield calculation. LP token + TCT burn. Settlement: fiat wire (T+1) or physical gold release (T+5 business days).",
            "acceptance_criteria": "Withdrawal request created. Liquidity check accurate. FIFO queue activates correctly when pool insufficient. Token burn reflected on-chain. Wire/gold release initiated within SLA."
        },
        {
            "feature_id": "AP-04",
            "feature_name": "Cash Withdrawal",
            "description": "Full or partial. Liquidity check. FIFO queue. Yield calculation to withdrawal date. LP + CCT burn. Fiat wire to registered bank account (T+1).",
            "acceptance_criteria": "Same as AP-03 for cash. Wire initiated within T+1. Correct yield calculated. LP share reduced accurately. FIFO queue position displayed to user."
        },
        {
            "feature_id": "AP-05",
            "feature_name": "Provider Dashboard",
            "description": "Real-time portfolio view: total deposited (gold oz / cash USD), current valuation (gold at LBMA price), LP share %, accumulated yield, projected APY, active trade exposure, withdrawal queue position (if any).",
            "acceptance_criteria": "Dashboard loads within 3 seconds. All values accurate within 1-minute lag. Gold price updates every 15 minutes. Yield updates daily. Export to PDF functional."
        },

        # Trade Management (TM)
        {
            "feature_id": "TM-01",
            "feature_name": "Trade Application",
            "description": "Upload trade documents (SBLC/ICPO/SCO/LOI, invoices, shipping docs), enter deal details (commodity, quantity, value, counterparty, proposed milestones). AI document verification. Application status tracking.",
            "acceptance_criteria": "Application submits successfully. All document types uploadable. AI verification returns confidence score. Status visible in Trader Portal. Admin notified of new application."
        },
        {
            "feature_id": "TM-02",
            "feature_name": "Credit Scoring",
            "description": "Risk assessment engine. MVP: Rules-based scoring with manual admin override (cold-start approach for 3 pilot deals with zero historical data). Factors: trade value, commodity type, counterparty profile, documentation quality. Future: XGBoost ML model.",
            "acceptance_criteria": "Credit score generated for every application. Rules produce consistent results. Admin can override with documented reason. Score affects credit limit assignment. History logged for future ML training."
        },
        {
            "feature_id": "TM-03",
            "feature_name": "Milestone Payments",
            "description": "Smart contract-managed 30/40/30 split. MilestonePayment.sol state machine: PENDING > APPROVED > RELEASED > COMPLETED (or DISPUTED). Escrow lock/release per milestone. Documentation verification at each gate.",
            "acceptance_criteria": "3 milestones created per trade. State transitions work correctly. Escrow locks/releases accurate amounts. Dispute transition functional. Cannot release without admin approval."
        },
        {
            "feature_id": "TM-04",
            "feature_name": "Trade NFT",
            "description": "ERC-721 NFT minted per trade deal, encoding: deal value, parties, commodity, milestones, terms, status. Immutable trade record on-chain. Status updates as milestones progress.",
            "acceptance_criteria": "NFT minted on deal creation. Metadata includes all deal terms. Status updates on milestone events. NFT queryable by all parties. Transfer restricted (soulbound to deal)."
        },
        {
            "feature_id": "TM-05",
            "feature_name": "Escrow Management",
            "description": "EscrowVault.sol locks financing amount from pool. Per-milestone release authorization. Refund capability for disputes. Emergency admin release with multisig.",
            "acceptance_criteria": "Funds lock correctly from pool. Release per milestone accurate to the wei. Refund works for dispute outcomes. Emergency release requires 3-of-5 multisig."
        },
        {
            "feature_id": "TM-06",
            "feature_name": "Repayment Tracking",
            "description": "Track buyer repayment against financing terms. Automated reminders. Late payment detection and penalty calculation (2% per 30-day period). Credit score impact for late/missed payments.",
            "acceptance_criteria": "Repayment schedule created on deal approval. Reminders sent at 7 days and 1 day before due. Late payment detected and penalty calculated accurately. Credit score updated on late event."
        },
        {
            "feature_id": "TM-07",
            "feature_name": "Chill Pay Off-Ramp",
            "description": "Supplier token-to-fiat conversion. Integration model: platform redirects supplier to Chill Pay hosted checkout (not embedded). Supported currencies: AED, USD. Settlement timeline: T+1 for AED, T+2 for USD. Chill Pay handles their own KYC.",
            "acceptance_criteria": "Supplier can initiate conversion from portal. Redirect to Chill Pay works. Conversion amount and currency correct. Settlement completes within SLA. Transaction status reflected back on platform."
        },

        # Compliance & Regulatory (CR)
        {
            "feature_id": "CR-01",
            "feature_name": "AML Screening",
            "description": "Automated sanctions screening on every transaction. GOAML integration for UAE compliance reporting. Suspicious Transaction Report (STR) generation. Watchlist matching (UN, OFAC, EU).",
            "acceptance_criteria": "Every deposit/withdrawal/trade triggers screening. Flagged transactions blocked pending review. GOAML-format reports exportable. STR generated with required fields. Watchlist updated weekly."
        },
        {
            "feature_id": "CR-02",
            "feature_name": "Transaction Monitoring",
            "description": "Real-time monitoring for unusual patterns: velocity, amount thresholds, geographic risk, behavioral anomalies. Alert generation for compliance officer review.",
            "acceptance_criteria": "Alerts generated for transactions exceeding thresholds. Compliance officer queue populated. False positive rate tracked. Alert resolution workflow complete. Audit trail for every alert."
        },
        {
            "feature_id": "CR-03",
            "feature_name": "Proof of Reserves",
            "description": "Real-time dashboard comparing: on-chain token supply (TCT + CCT) versus off-chain verified reserves (gold + cash). Backing ratio displayed. Alert if ratio approaches 100%.",
            "acceptance_criteria": "Dashboard accurate within 15-minute lag. Backing ratio calculation correct. Alert fires at 102% threshold. Historical backing ratio trend visible. Exportable for regulator reporting."
        },
        {
            "feature_id": "CR-04",
            "feature_name": "Risk Dashboard",
            "description": "Unified risk view: pool utilization %, concentration per trade, default probability scores, provider exposure breakdown, active dispute count, regulatory compliance status.",
            "acceptance_criteria": "Dashboard loads with all risk metrics. Pool utilization real-time. Concentration limit violations flagged. All data points sourced from live platform data."
        },
        {
            "feature_id": "CR-05",
            "feature_name": "Regulator Observer",
            "description": "Read-only portal for VARA compliance auditors. Transaction browser, audit trail viewer, compliance report generator, real-time pool status. No write/modify capability.",
            "acceptance_criteria": "Observer can log in with restricted role. All transactions viewable. Audit trail searchable by date/type/party. No edit/delete capabilities available. Session logging active."
        },
        {
            "feature_id": "CR-06",
            "feature_name": "Audit Trail",
            "description": "Immutable log of every platform action: user actions, admin decisions, smart contract events, system events. Tamper-evident (hash-chained). Searchable and exportable.",
            "acceptance_criteria": "Every action logged with timestamp, user, action type, before/after state. Logs hash-chained for tamper detection. Search by date range, user, action type. Export to CSV/PDF."
        },

        # Yield Engine (YE)
        {
            "feature_id": "YE-01",
            "feature_name": "Yield Calculation",
            "description": "Daily calculation of provider yield from trade financing fees. Formula: Provider Yield = (LP Share % x 70% of Total Fees Collected) / Time Period. Compounding frequency: daily accrual, monthly distribution.",
            "acceptance_criteria": "Yield calculated daily at UTC midnight. Accurate to 6 decimal places. Matches manual calculation within 0.01% tolerance. Handles partial-period deposits correctly."
        },
        {
            "feature_id": "YE-02",
            "feature_name": "Distribution Trigger",
            "description": "Monthly automated yield distribution to all active LP holders. Pro-rata based on time-weighted average LP share. Distribution event logged on-chain. Provider notification sent.",
            "acceptance_criteria": "Distribution executes monthly. All eligible providers receive correct amounts. On-chain distribution event emitted. Email/portal notification sent. Distribution history viewable."
        },
        {
            "feature_id": "YE-03",
            "feature_name": "LP Value Update",
            "description": "Real-time LP token value calculation. Factors: underlying asset value (gold at LBMA + cash at par), accumulated yield, pool utilization. LP price displayed on Provider Dashboard.",
            "acceptance_criteria": "LP value updates every 15 minutes. Reflects gold price changes, new deposits, withdrawals, and yield accrual. Historical LP value chart available."
        }
    ]

    for item in items:
        # Construct the expected Jira output based on INVEST and Golden Structure
        expected_output = {
            "summary": f"Implement {item['feature_name']}",
            "description": f"**Context/Value:**\nAs a system user, I want {item['feature_name'].lower()} so that the platform can support its core operational and compliance needs.\n\n**Details:**\n{item['description']}",
            "issue_type": "Story",
            "acceptance_criteria": [ac.strip() for ac in item['acceptance_criteria'].split('.') if ac.strip()]
        }

        print(f"Adding item: {item['feature_id']}...")
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input={"feature_id": item["feature_id"], "feature_name": item["feature_name"], "description": item["description"], "acceptance_criteria": item["acceptance_criteria"]},
            expected_output=expected_output
        )

    print(f"Dataset '{dataset_name}' successfully seeded with {len(items)} items!")

if __name__ == "__main__":
    seed_dataset()
