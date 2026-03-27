
import os
from jira import JIRA
from dotenv import load_dotenv

def test_api():
    load_dotenv()
    server = os.environ.get("JIRA_SERVER")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")
    project_key = os.environ.get("JIRA_PROJECT_KEY")

    print(f"Testing API connection to: {server}")
    try:
        jira = JIRA(server=server, basic_auth=(email, token))
        myself = jira.myself()
        print(f"Successfully authenticated as: {myself.get('displayName')}")
        project = jira.project(project_key)
        print(f"Successfully accessed project: {project.name}")
        print("API test PASSED!")
    except Exception as e:
        print(f"API test FAILED: {e}")

if __name__ == "__main__":
    test_api()
