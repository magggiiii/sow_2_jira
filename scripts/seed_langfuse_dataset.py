import os
from langfuse import Langfuse

# Initialize Langfuse client
# In local dev with admin compose, Langfuse runs on port 3002
os.environ["LANGFUSE_PUBLIC_KEY"] = os.getenv("LANGFUSE_PUBLIC_KEY", "pk-lf-1234567890")
os.environ["LANGFUSE_SECRET_KEY"] = os.getenv("LANGFUSE_SECRET_KEY", "sk-lf-1234567890")
os.environ["LANGFUSE_HOST"] = os.getenv("LANGFUSE_HOST", "http://localhost:3002")

def seed_dataset():
    langfuse = Langfuse()

    dataset_name = "sow-extraction-ground-truth"
    
    print(f"Creating/getting dataset: {dataset_name}...")
    langfuse.create_dataset(
        name=dataset_name,
        description="Ground truth Jira tickets extracted from the Raw Essentials SOW MVP Phase 0.",
        metadata={"domain": "trade-finance", "source": "test_sow.pdf"}
    )

    # Define Ground Truth items based on the SOW
    items = [
        {
            "input": {
                "feature_id": "IAM-01",
                "feature_name": "User Registration",
                "description": "Self-service registration for Providers and Traders. Entity type selection (Gold Provider, Cash Provider, Trader), email verification, profile completion with company details.",
                "acceptance_criteria": "User registers, verifies email, selects entity type, completes profile. Duplicate email prevented. Registration event logged. Admin notified of new registration."
            },
            "expected_output": {
                "summary": "Implement Self-Service User Registration",
                "description": "As a new user (Provider or Trader), I want to register and complete my profile so that I can access the platform.\n\n**Details:**\n- Entity type selection (Gold Provider, Cash Provider, Trader)\n- Email verification\n- Profile completion with company details",
                "issue_type": "Story",
                "acceptance_criteria": [
                    "User can register and verify email.",
                    "User can select entity type and complete profile.",
                    "Duplicate emails are prevented.",
                    "Registration events are logged.",
                    "Admin is notified of new registrations."
                ]
            }
        },
        {
            "input": {
                "feature_id": "AP-01",
                "feature_name": "Gold Deposit",
                "description": "Multi-step workflow: gold details entry (weight, purity, custodian, vault), 4-document upload (Certificate of Ownership, Assay Report, Insurance, Custodian Confirmation), custodian verification (email-based MVP), compliance check, admin approval, on-chain registration, TCT minting.",
                "acceptance_criteria": "Full flow from entry to TCT minting. All 4 documents uploadable (PDF, max 10MB each). Custodian verification email sent with template. Admin approval triggers on-chain registration. Rejection requires reason."
            },
            "expected_output": {
                "summary": "Implement Gold Deposit Workflow and TCT Minting",
                "description": "As a Gold Provider, I want to submit a gold deposit request with required documentation so that I can receive TCT tokens.\n\n**Details:**\n- Multi-step wizard for gold details (weight, purity, custodian, vault)\n- 4-document upload (Certificate of Ownership, Assay, Insurance, Custodian Confirmation)\n- Admin approval triggers on-chain registration and TCT minting.",
                "issue_type": "Story",
                "acceptance_criteria": [
                    "Providers can enter gold details and upload 4 PDF documents (max 10MB each).",
                    "System sends custodian verification email using standard template.",
                    "Admin approval successfully triggers on-chain TCT minting.",
                    "Rejections mandate a reason to be provided."
                ]
            }
        },
        {
            "input": {
                "feature_id": "TM-02",
                "feature_name": "Credit Scoring",
                "description": "Risk assessment engine. MVP: Rules-based scoring with manual admin override (cold-start approach for 3 pilot deals with zero historical data). Factors: trade value, commodity type, counterparty profile, documentation quality. Future: XGBoost ML model.",
                "acceptance_criteria": "Credit score generated for every application. Rules produce consistent results. Admin can override with documented reason. Score affects credit limit assignment. History logged for future ML training."
            },
            "expected_output": {
                "summary": "Develop Rules-Based Credit Scoring Engine",
                "description": "As an Admin, I want the system to calculate a rules-based credit score for new trade applications so that risk can be assessed consistently.\n\n**Details:**\n- Factors: trade value, commodity type, counterparty profile, documentation quality\n- Manual admin override required for MVP.",
                "issue_type": "Story",
                "acceptance_criteria": [
                    "Engine generates a consistent credit score for every application.",
                    "Admins can override the score with a required documented reason.",
                    "Calculated score correctly limits the assigned credit limit.",
                    "Scoring history is securely logged for future ML training."
                ]
            }
        }
    ]

    for item in items:
        print(f"Adding item: {item['input']['feature_id']}...")
        langfuse.create_dataset_item(
            dataset_name=dataset_name,
            input=item["input"],
            expected_output=item["expected_output"]
        )

    print(f"Dataset '{dataset_name}' successfully seeded with {len(items)} items!")

if __name__ == "__main__":
    seed_dataset()
